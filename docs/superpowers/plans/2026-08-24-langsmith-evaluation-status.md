# LangSmith Evaluation Status Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Publish the individual-agent harness's authoritative final status and one deterministic, secret-safe failure reason as experiment-level LangSmith feedback and UI-visible project metadata without changing the local quality verdict, row metrics, artifacts, or exit codes.

**Architecture:** Keep status authority in `evaluation.runner`: one pure status-value builder calls the existing `decide_status`, while a synchronous closure reads the repetition results already populated by row evaluators and returns the two LangSmith project-feedback values. Pass that closure through `aevaluate(summary_evaluators=[...])`; after evaluation, read–merge–update the same two values into the experiment project's metadata using the public `experiment_id`. Wrap both observability paths so upload/read/update failures appear in `ExperimentResult.errors` but cannot rewrite the local quality verdict.

**Tech Stack:** Python 3.11+, Pydantic v2 contracts, LangSmith 0.10.11 behavior (`langsmith>=0.10` in `pyproject.toml`), pytest + pytest-asyncio, Ruff. No new dependency.

## Global Constraints

- The approved design at `docs/superpowers/specs/2026-08-24-langsmith-evaluation-status-design.md` is authoritative.
- The experiment-level feedback keys are exactly `evaluation_status` and `evaluation_failure_reason`.
- `evaluation_status` uses only the existing `EvaluationStatus` literals: `REVIEW REQUIRED`, `FAILED`, and `INFRASTRUCTURE FAILURE`.
- The status summary runs after the target and both existing row evaluators, when every completed repetition has been added to `repetitions_by_case`.
- Dynamic failure-summary content is restricted to case IDs, repetition numbers, gate IDs, and typed `EvaluationFailure.reason` / `JudgeNotRunReason` values. Never read or include provider exception messages, `EvaluationFailure.message`, gate details, judge rationales, prompts, target outputs, run inputs, trace payloads, or secrets.
- A missing or failed status-summary upload is recorded as `EvaluationFailure(stage="trace", reason="langsmith_summary_unavailable", ...)`, but is excluded from the inputs to `decide_status`; the pre-summary local quality verdict remains unchanged.
- A missing or failed project-metadata publication is recorded as `EvaluationFailure(stage="trace", reason="langsmith_project_metadata_unavailable", ...)`, but is excluded from the inputs to `decide_status`; the pre-publication local quality verdict remains unchanged.
- If `aevaluate` fails before it invokes the summary evaluator, preserve the existing `langsmith_unavailable` infrastructure-failure path and do not fabricate summary feedback.
- Do not change gate definitions, judge weights, score thresholds, repetitions, exit codes, row-level feedback, artifact schema, LangSmith project names, or `LANGSMITH_ENDPOINT`.
- Do not mark an individual target or judge run as errored merely because aggregate status is `FAILED`.
- Project metadata publication must preserve all existing metadata and replace only `evaluation_status` and `evaluation_failure_reason`.
- Every test is offline and fake-driven. No test may call LangSmith, DeepSeek, OpenAI, Tavily, or another live provider.
- Do not dispatch DeepSeek for implementation or review. Use the caller-approved non-DeepSeek routing.
- Preserve the six pre-existing worktree modifications in `src/deep_research/evaluation/cli.py`, `src/deep_research/evaluation/judging.py`, `src/deep_research/evaluation/runner.py`, `tests/evaluation_fakes.py`, `tests/test_evaluation/test_judge_visibility.py`, and `tests/test_evaluation/test_runner.py`. Do not reset or overwrite them.
- Because this plan overlaps four dirty files, stage only task-specific hunks with `git add -p`, inspect `git diff --cached`, and leave unrelated existing hunks unstaged unless their owner has committed them separately.
- The global editable install currently points at the main checkout. Run pytest through the worktree-first command shown in each task so imports resolve from this linked worktree.

---

## Verified LangSmith SDK Behavior

The plan is based on the locally installed LangSmith 0.10.11 source, not on an assumed API shape:

- `aevaluate(..., summary_evaluators=...)` accepts synchronous callables shaped as `Callable[[Sequence[Run], Sequence[Example]], EvaluationResult | EvaluationResults]`.
- `_aevaluate` wires predictions and row evaluators first, then calls `awith_summary_evaluators`; `_aapply_summary_evaluators` drains all runs and examples before invoking each summary evaluator.
- Each returned summary result is submitted with `Client.create_feedback(run_id=None, project_id=<experiment id>, ...)`, which makes it experiment-level feedback.
- `_aapply_summary_evaluators` catches and logs both evaluator exceptions and `create_feedback` exceptions instead of raising them through `aevaluate`.
- Therefore, the runner must catch its own summary-construction failures and use a client proxy to observe SDK-swallowed project-feedback submission failures. It must not inspect private `AsyncExperimentResults` fields or perform an eventually-consistent readback request.

## File Structure

| File | Responsibility in this change |
| --- | --- |
| `src/deep_research/evaluation/runner.py` | Preserve focused `dataset_examples` and experiment URLs; define the pure status/failure-reason summary contract, build case results through one shared helper, pass the synchronous summary evaluator to `aevaluate`, publish UI-visible project metadata, observe project-feedback/metadata submission failures, and keep auxiliary feedback/URL failures verdict-neutral. |
| `src/deep_research/evaluation/cli.py` | Select exactly one LangSmith dataset example per focused case, reject missing/duplicate/wrong identities, and pass the already-created LangSmith client into `run_agent_evaluation`. |
| `src/deep_research/evaluation/judging.py` | Bind judge-provider execution to the target repetition's session span so provider telemetry has a valid trace parent. |
| `tests/evaluation_fakes.py` | Make `FakeEvaluateRunner` support selected mapping/object examples, preserve experiment URLs, reproduce LangSmith's post-row summary timing and swallowed upload failures, and make `FakeLangSmithClient` record or deliberately fail project feedback without network access. |
| `tests/test_evaluation/test_cli.py` | Prove focused-case dataset selection is exact, ordered, duplicate-safe, and offline. |
| `tests/test_evaluation/test_judge_visibility.py` | Prove the judge opens the target repetition's exact session context before the provider call and closes it afterward. |
| `tests/test_evaluation/test_runner.py` | Prove selected data cannot create wrong-case rows, experiment URLs survive without changing status, all three summaries are exact and safe, summary timing is correct, artifacts are unchanged, and summary upload failure is verdict-neutral. |
| `tests/test_evaluation/conftest.py` | Inject a fresh fake LangSmith client into each offline suite experiment. |
| `tests/test_evaluation/test_suite.py` | Verify every suite member passes a status summary and an injected client without changing suite behavior. |

No change is planned for `models.py`, `reporting.py`, configuration, dataset synchronization, gates, judging weights, or artifact schemas.

---

### Task 1: Review and Harden the Existing Planner-Enablement Changes

**Files:**
- Modify: `src/deep_research/evaluation/cli.py:277-342`
- Modify: `src/deep_research/evaluation/judging.py:435-492,553-584`
- Modify: `src/deep_research/evaluation/runner.py:692-900`
- Modify: `tests/evaluation_fakes.py:29-136`
- Modify: `tests/test_evaluation/test_cli.py:1-260`
- Modify: `tests/test_evaluation/test_judge_visibility.py:1-170`
- Modify: `tests/test_evaluation/test_runner.py:167-480`

**Interfaces:**
- Consumes:
  - LangSmith dataset examples with `inputs`, `outputs`, and `metadata` attributes.
  - Focused runtime identity `runtime.case_id` and the exact `EvaluationCase.identity` tuple `(case_id, version)`.
  - `Tracker.session_span(session_id: str, question: str)` and the target `TargetOutput.session_id`.
  - `AsyncExperimentResults.url` and async `get_comparison_url()` from LangSmith 0.10.11.
- Produces:
  - `_focused_dataset_examples(langsmith_client: Any, runtime: EvaluationRuntimeConfig, cases: Sequence[EvaluationCase]) -> tuple[Any, ...] | None` in `cli.py`.
  - `build_judge_evaluator(..., tracker: Tracker | None = None, ...) -> JudgeEvaluator`; when a tracker is supplied, the provider call runs inside the target repetition's exact session context.
  - `_validate_dataset_examples(dataset_examples: Sequence[Any], cases: Sequence[EvaluationCase]) -> None` in `runner.py`; the direct runner boundary rejects missing, duplicate, malformed, or wrong-case focused rows before `aevaluate` starts.
  - `run_agent_evaluation(..., dataset_examples: Sequence[Any] | None = None, ...) -> ExperimentResult`; a valid supplied sequence is passed to `aevaluate(data=...)` unchanged and the returned experiment URL is preserved.
  - Fake support for both mapping examples and LangSmith-shaped object examples, so focused-path tests exercise the production data shape.

- [ ] **Step 1: Record and preserve the existing dirty baseline**

Run:

```powershell
git status --short
git diff -- src/deep_research/evaluation/cli.py src/deep_research/evaluation/judging.py src/deep_research/evaluation/runner.py tests/evaluation_fakes.py tests/test_evaluation/test_judge_visibility.py tests/test_evaluation/test_runner.py
```

Expected: the six known modified files and no additional production/test file. Save this diff as the baseline for review; do not reset it. The review must retain:

```text
cli.py: focused-case dataset-example filtering
judging.py: tracker injection and judge session-span binding
runner.py: dataset_examples, tracker injection, and experiment_url preservation
evaluation_fakes.py: selected-data support and FakeExperimentResults.url
test_judge_visibility.py: tracker-bound judge coverage
test_runner.py: focused-data and experiment-URL coverage
```

- [ ] **Step 2: Add failing tests for exact focused-case dataset selection**

Import `_focused_dataset_examples`, `PreflightError`, `FakeDataset`, and `FakeLangSmithClient` in `test_cli.py`. Add a small dataset builder and these tests:

```python
def _dataset_client(name, examples):
    dataset = FakeDataset(name, "dataset-1")
    client = FakeLangSmithClient(datasets=[dataset])
    client.create_examples(dataset_id=dataset.id, examples=examples)
    return client


def test_focused_selection_returns_only_the_requested_case_in_case_order(
    runtime_config_for, planner_case
) -> None:
    other = {
        "inputs": {"case_id": "other-case"},
        "outputs": {},
        "metadata": {"case_id": "other-case", "case_version": 1},
    }
    wanted = {
        "inputs": {"case_id": planner_case.case_id},
        "outputs": {},
        "metadata": {
            "case_id": planner_case.case_id,
            "case_version": planner_case.version,
        },
    }
    runtime = runtime_config_for("planner", case_id=planner_case.case_id)
    client = _dataset_client(runtime.dataset_name, [other, wanted])

    selected = _focused_dataset_examples(
        client, runtime, [planner_case]
    )

    assert selected is not None
    assert len(selected) == 1
    assert selected[0].metadata == wanted["metadata"]


@pytest.mark.parametrize("copies", [0, 2])
def test_focused_selection_rejects_missing_or_duplicate_case_rows(
    runtime_config_for, planner_case, copies
) -> None:
    runtime = runtime_config_for("planner", case_id=planner_case.case_id)
    payload = {
        "inputs": {"case_id": planner_case.case_id},
        "outputs": {},
        "metadata": {
            "case_id": planner_case.case_id,
            "case_version": planner_case.version,
        },
    }
    client = _dataset_client(runtime.dataset_name, [payload] * copies)

    with pytest.raises(PreflightError) as captured:
        _focused_dataset_examples(client, runtime, [planner_case])

    assert captured.value.reason == "dataset_unavailable"
```

Also add a non-focused test asserting `_focused_dataset_examples(...) is None`; full-agent and suite runs must continue passing the dataset name rather than materializing every example.

- [ ] **Step 3: Run the focused-selection tests to verify red**

```powershell
python -c "import sys; sys.path.insert(0, r'src'); import pytest; raise SystemExit(pytest.main(['tests/test_evaluation/test_cli.py','-k','focused_selection','-q']))"
```

Expected: collection fails because `_focused_dataset_examples` does not yet exist.

- [ ] **Step 4: Extract and harden focused selection in `cli.py`**

Import `Mapping` from `collections.abc`. Add this helper above `_run_agent_pipeline`:

```python
def _focused_dataset_examples(
    langsmith_client: Any,
    runtime: EvaluationRuntimeConfig,
    cases: Sequence[EvaluationCase],
) -> tuple[Any, ...] | None:
    if runtime.case_id is None:
        return None

    selected_identities = {case.identity for case in cases}
    examples_by_identity: dict[tuple[str, int], Any] = {}
    for example in langsmith_client.list_examples(
        dataset_name=runtime.dataset_name
    ):
        metadata = example.metadata
        if not isinstance(metadata, Mapping):
            continue
        identity = (metadata.get("case_id"), metadata.get("case_version"))
        if identity not in selected_identities:
            continue
        if identity in examples_by_identity:
            raise PreflightError(
                "dataset_unavailable",
                "synced dataset contains a duplicate selected case",
            )
        examples_by_identity[identity] = example

    if set(examples_by_identity) != selected_identities:
        raise PreflightError(
            "dataset_unavailable",
            "synced dataset does not contain every selected case",
        )
    return tuple(examples_by_identity[case.identity] for case in cases)
```

Replace the inline filtering block in `_run_agent_pipeline` with:

```python
dataset_examples = _focused_dataset_examples(
    langsmith_client, runtime, cases
)
```

This exact-identity map closes the count-only loophole: a duplicate cannot stand in for a missing identity, unrelated rows are ignored, and output ordering follows `cases` rather than remote listing order.

- [ ] **Step 5: Make `FakeEvaluateRunner` accept mapping and object examples**

Add this helper in `tests/evaluation_fakes.py`:

```python
def _example_payload(example: Any) -> dict[str, Any]:
    if isinstance(example, Mapping):
        return dict(example)
    return {
        "inputs": dict(example.inputs),
        "outputs": dict(example.outputs),
        "metadata": dict(example.metadata),
    }
```

In `FakeEvaluateRunner.__call__`, normalize selected data before invoking the target:

```python
raw_examples = self.examples if isinstance(data, str) else list(data)
examples = [_example_payload(example) for example in raw_examples]
```

Keep the existing repetition loop, row evaluators, and row recording unchanged. This lets a test pass the actual `FakeExample` objects returned by `FakeLangSmithClient.list_examples`, matching production's LangSmith `Example` shape.

- [ ] **Step 6: Validate selected identities again at the runner boundary**

Add a runner-side metadata reader and validator before `run_agent_evaluation`:

```python
def _dataset_example_identity(example: Any) -> tuple[str, int] | None:
    if isinstance(example, Mapping):
        metadata = example.get("metadata")
    else:
        metadata = getattr(example, "metadata", None)
    if not isinstance(metadata, Mapping):
        return None
    case_id = metadata.get("case_id")
    case_version = metadata.get("case_version")
    if (
        not isinstance(case_id, str)
        or not isinstance(case_version, int)
        or isinstance(case_version, bool)
    ):
        return None
    return case_id, case_version


def _validate_dataset_examples(
    dataset_examples: Sequence[Any], cases: Sequence[EvaluationCase]
) -> None:
    expected = [case.identity for case in cases]
    actual = [_dataset_example_identity(example) for example in dataset_examples]
    if (
        any(identity is None for identity in actual)
        or len(actual) != len(expected)
        or set(actual) != set(expected)
    ):
        raise PreflightError(
            "dataset_unavailable",
            "selected dataset examples do not exactly match requested cases",
        )
```

At the start of `run_agent_evaluation`, before building the target, call it only when `dataset_examples is not None`:

```python
if dataset_examples is not None:
    _validate_dataset_examples(dataset_examples, cases)
```

The length-plus-set comparison rejects duplicates, omissions, malformed metadata, and wrong identities while allowing the exact case-ordered tuple produced by the CLI helper.

- [ ] **Step 7: Prove selected data cannot produce wrong-case rows**

Replace the focused runner test's mapping-only input with `FakeExample` objects selected through `_focused_dataset_examples`, then assert every target row identity is the requested identity:

```python
assert runner.calls[0]["data"] == selected
assert len(runner.rows) == runtime.repetitions
assert {
    (row["outputs"]["case_id"], row["outputs"]["case_version"])
    for row in runner.rows
} == {focused.cases[0].identity}
```

Add one direct guard test that passes a selected sequence containing a wrong-case example and asserts `run_agent_evaluation` raises `PreflightError(reason="dataset_unavailable")` before `runner.calls` gains an entry. A wrong row must never reach the target, populate `repetitions_by_case`, contribute a score, or yield the misleading empty-cases status `REVIEW REQUIRED`.

- [ ] **Step 8: Strengthen the judge session-span test**

Add a recording tracker double to `test_judge_visibility.py`:

```python
class RecordingSessionTracker:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []
        self.active = False

    @asynccontextmanager
    async def session_span(self, session_id, question):
        self.calls.append((session_id, question))
        self.active = True
        try:
            yield
        finally:
            self.active = False
```

Import `asynccontextmanager`. Add a provider double whose `complete_structured` asserts `tracker.active is True`. Use it in a test and assert:

```python
assert tracker.calls == [
    (
        clean_target_output.session_id,
        planner_case.state.original_question,
    )
]
assert tracker.active is False
```

Retain the existing `TrackerBoundStructuredProvider` test as integration coverage for the real `Tracker.llm_span` contract. Together the tests prove the judge has a session parent, uses the target repetition's session ID rather than a new ID, and restores context after success.

- [ ] **Step 9: Review experiment URL preservation without status distortion**

Extend `FakeExperimentResults` so tests can cover both public SDK paths:

```python
@dataclass
class FakeExperimentResults:
    experiment_name: str
    url: str | None
    comparison_url: str | None = None
    comparison_error: Exception | None = None
    rows: list[dict[str, Any]] = field(default_factory=list)

    async def get_comparison_url(self) -> str | None:
        if self.comparison_error is not None:
            raise self.comparison_error
        return self.comparison_url
```

Add runner tests for:

```python
assert result.experiment_url == direct_url
assert result.status == status_before_url_read
```

and, with `url=None`:

```python
assert result.experiment_url == comparison_url
```

Finally, configure `comparison_error=ConnectionError("url unavailable")` and assert the already-computed quality status is unchanged, `experiment_url is None`, and `result.errors` contains `EvaluationFailure(stage="trace", reason="experiment_url_unavailable", ...)`. Treat this as auxiliary observability damage, not as an `aevaluate` transport failure; otherwise a display-link failure would misleadingly replace a valid quality verdict with `INFRASTRUCTURE FAILURE`.

Implement that distinction by keeping `evaluation_errors` (used by `decide_status`) separate from `auxiliary_errors` (persisted but verdict-neutral), and by moving URL extraction into the successful `else` branch of the `try/except await evaluate` block with its own safe `try/except`.

- [ ] **Step 10: Run all planner-enablement tests and Ruff**

```powershell
python -c "import sys; sys.path.insert(0, r'src'); import pytest; raise SystemExit(pytest.main(['tests/test_evaluation/test_cli.py','tests/test_evaluation/test_judge_visibility.py','tests/test_evaluation/test_runner.py','-q']))"
python -m ruff check src/deep_research/evaluation/cli.py src/deep_research/evaluation/judging.py src/deep_research/evaluation/runner.py tests/evaluation_fakes.py tests/test_evaluation/test_cli.py tests/test_evaluation/test_judge_visibility.py tests/test_evaluation/test_runner.py
```

Expected: all selected tests pass and Ruff reports `All checks passed!`. Required logical assertions are: no wrong-case row is scored as the focused case, every tracker-bound judge provider runs under the exact target session, direct/fallback experiment URLs are preserved, and a URL lookup failure cannot rewrite the local quality status.

- [ ] **Step 11: Review and commit the planner-enablement changes as one scoped unit**

```powershell
git diff --check
git diff -- src/deep_research/evaluation/cli.py src/deep_research/evaluation/judging.py src/deep_research/evaluation/runner.py tests/evaluation_fakes.py tests/test_evaluation/test_cli.py tests/test_evaluation/test_judge_visibility.py tests/test_evaluation/test_runner.py
git add -p -- src/deep_research/evaluation/cli.py src/deep_research/evaluation/judging.py src/deep_research/evaluation/runner.py tests/evaluation_fakes.py tests/test_evaluation/test_cli.py tests/test_evaluation/test_judge_visibility.py tests/test_evaluation/test_runner.py
git diff --cached --check
git diff --cached
git commit -m "fix: enable reliable focused planner evaluation"
```

Expected staged diff: the reviewed pre-existing planner-enablement hunks plus the exact hardening/tests above. No status-summary implementation from later tasks is staged in this commit.

---

### Task 2: Define the Pure Status and Safe Failure-Reason Contract

**Files:**
- Modify: `src/deep_research/evaluation/runner.py:393-568`
- Modify: `tests/test_evaluation/test_runner.py:1-165`

**Interfaces:**
- Consumes:
  - `decide_status(cases: Sequence[CaseResult], *, tier: EvaluationTier, runtime: EvaluationRuntimeConfig, errors: Sequence[EvaluationFailure] = ()) -> EvaluationStatus`
  - `CaseResult`, `RepetitionResult`, `GateReport.failed_ids`, `EvaluationFailure.stage`, `EvaluationFailure.reason`, and `JudgeFeedback.not_run_reason`
- Produces:
  - `_quality_thresholds(runtime: EvaluationRuntimeConfig) -> tuple[float, float]`, returning `(case_average_threshold, repetition_floor)` for controlled runs and `(live_threshold, live_threshold)` for live runs.
  - `evaluation_failure_reason(cases: Sequence[CaseResult], *, status: EvaluationStatus, runtime: EvaluationRuntimeConfig, errors: Sequence[EvaluationFailure] = ()) -> str`
  - `build_evaluation_summary_feedback(cases: Sequence[CaseResult], *, tier: EvaluationTier, runtime: EvaluationRuntimeConfig, errors: Sequence[EvaluationFailure] = ()) -> dict[str, list[dict[str, JsonValue]]]`

- [ ] **Step 1: Write failing tests for exact status values and deterministic reasons**

Add the imports `EvaluationFailure`, `GateReport`, and `GateResult`, plus `build_evaluation_summary_feedback`, to `tests/test_evaluation/test_runner.py`. Add this local reader and the three status tests:

```python
def _summary_values(payload) -> dict[str, str]:
    return {item["key"]: item["value"] for item in payload["results"]}


def test_the_summary_feedback_matches_the_local_passing_status(
    passing_cases, runtime_config_for
) -> None:
    runtime = runtime_config_for("planner")
    payload = build_evaluation_summary_feedback(
        passing_cases, tier="controlled", runtime=runtime
    )

    assert _summary_values(payload) == {
        "evaluation_status": decide_status(
            passing_cases, tier="controlled", runtime=runtime
        ),
        "evaluation_failure_reason": (
            "all cases passed automated checks; human review required"
        ),
    }


def test_the_summary_feedback_names_the_first_failed_gate(
    failing_experiment_result, runtime_config_for
) -> None:
    payload = build_evaluation_summary_feedback(
        failing_experiment_result.cases,
        tier="controlled",
        runtime=runtime_config_for("synthesizer"),
    )

    assert _summary_values(payload) == {
        "evaluation_status": "FAILED",
        "evaluation_failure_reason": (
            "unsupported-claim repetition 1 failed citations_known"
        ),
    }


def test_the_summary_feedback_names_a_typed_infrastructure_reason(
    runtime_config_for,
) -> None:
    errors = [
        EvaluationFailure(
            stage="trace",
            reason="langsmith_unavailable",
            message="transport text must not be summarized",
        )
    ]
    payload = build_evaluation_summary_feedback(
        [],
        tier="controlled",
        runtime=runtime_config_for("planner"),
        errors=errors,
    )

    assert _summary_values(payload) == {
        "evaluation_status": "INFRASTRUCTURE FAILURE",
        "evaluation_failure_reason": "trace:langsmith_unavailable",
    }
```

- [ ] **Step 2: Run the tests to verify the missing interface fails**

Run:

```powershell
python -c "import sys; sys.path.insert(0, r'src'); import pytest; raise SystemExit(pytest.main(['tests/test_evaluation/test_runner.py','-k','summary_feedback','-q']))"
```

Expected: collection fails because `build_evaluation_summary_feedback` is not defined in `deep_research.evaluation.runner`.

- [ ] **Step 3: Implement the minimal status and failure-reason helpers**

Add these helpers immediately after `decide_status` in `runner.py`:

```python
def _quality_thresholds(
    runtime: EvaluationRuntimeConfig,
) -> tuple[float, float]:
    if runtime.tier == "live":
        return runtime.live_threshold, runtime.live_threshold
    return runtime.case_average_threshold, runtime.repetition_floor


def evaluation_failure_reason(
    cases: Sequence[CaseResult],
    *,
    status: EvaluationStatus,
    runtime: EvaluationRuntimeConfig,
    errors: Sequence[EvaluationFailure] = (),
) -> str:
    """Return one deterministic summary built only from safe identifiers."""
    if status == "REVIEW REQUIRED":
        return "all cases passed automated checks; human review required"

    infrastructure = next(
        (error for error in errors if error.stage in ("trace", "setup")),
        None,
    )
    if infrastructure is not None:
        return f"{infrastructure.stage}:{infrastructure.reason}"

    threshold, floor = _quality_thresholds(runtime)
    for case in cases:
        for repetition in sorted(
            case.repetitions, key=lambda item: item.repetition
        ):
            if repetition.errors:
                return (
                    f"{case.case_id} repetition {repetition.repetition} "
                    f"failed {repetition.errors[0].reason}"
                )
            if repetition.gates.failed_ids:
                return (
                    f"{case.case_id} repetition {repetition.repetition} "
                    f"failed {repetition.gates.failed_ids[0]}"
                )
            if not repetition.completed:
                return (
                    f"{case.case_id} repetition {repetition.repetition} "
                    "failed run_incomplete"
                )
            if repetition.judge is None:
                return (
                    f"{case.case_id} repetition {repetition.repetition} "
                    "failed judge_missing"
                )
            if repetition.judge.status == "judge_not_run":
                return (
                    f"{case.case_id} repetition {repetition.repetition} "
                    f"failed {repetition.judge.not_run_reason}"
                )
            if repetition.aggregate_quality is None:
                return (
                    f"{case.case_id} repetition {repetition.repetition} "
                    "failed aggregate_quality_unavailable"
                )
            if repetition.aggregate_quality < floor:
                return (
                    f"{case.case_id} repetition {repetition.repetition} "
                    "failed repetition_floor"
                )
        if case.average_quality is None:
            return f"{case.case_id} failed case_average_unavailable"
        if case.average_quality < threshold:
            return f"{case.case_id} failed case_average_threshold"
        if not case.passed:
            return f"{case.case_id} failed case_result"

    return "evaluation failed"


def build_evaluation_summary_feedback(
    cases: Sequence[CaseResult],
    *,
    tier: EvaluationTier,
    runtime: EvaluationRuntimeConfig,
    errors: Sequence[EvaluationFailure] = (),
) -> dict[str, list[dict[str, JsonValue]]]:
    status = decide_status(cases, tier=tier, runtime=runtime, errors=errors)
    return {
        "results": [
            {"key": "evaluation_status", "value": status},
            {
                "key": "evaluation_failure_reason",
                "value": evaluation_failure_reason(
                    cases,
                    status=status,
                    runtime=runtime,
                    errors=errors,
                ),
            },
        ]
    }
```

Do not use `runs` or `examples` in these helpers. They must operate only on the typed local contract.

- [ ] **Step 4: Run the status tests and verify green**

Run:

```powershell
python -c "import sys; sys.path.insert(0, r'src'); import pytest; raise SystemExit(pytest.main(['tests/test_evaluation/test_runner.py','-k','summary_feedback','-q']))"
```

Expected: 3 selected tests pass.

- [ ] **Step 5: Add the secret-exclusion regression test**

Add this test. It deliberately puts a secret in fields the summary builder is forbidden to read and asserts that only safe IDs escape:

```python
def test_the_failure_summary_never_reads_messages_details_or_rationales(
    repetition_with_failed_gate, runtime_config_for
) -> None:
    secret = "sk-summary-must-not-leak-123456"
    failure = EvaluationFailure(
        stage="provider",
        reason="provider_failure",
        message=f"provider rejected key={secret}",
    )
    repetition = repetition_with_failed_gate.model_copy(
        update={
            "errors": [failure],
            "gates": GateReport(
                results=[
                    GateResult(
                        gate_id="prioritized_subtopics",
                        passed=False,
                        detail=f"raw output contained {secret}",
                    )
                ]
            ),
        }
    )
    case = build_case_result(None, [repetition], threshold=0.80)

    payload = build_evaluation_summary_feedback(
        [case],
        tier="controlled",
        runtime=runtime_config_for("planner"),
    )

    assert _summary_values(payload)["evaluation_failure_reason"] == (
        "focused-decomposition repetition 98 failed provider_failure"
    )
    assert secret not in repr(payload)
```

- [ ] **Step 6: Run the focused runner tests and Ruff**

Run:

```powershell
python -c "import sys; sys.path.insert(0, r'src'); import pytest; raise SystemExit(pytest.main(['tests/test_evaluation/test_runner.py','-q']))"
python -m ruff check src/deep_research/evaluation/runner.py tests/test_evaluation/test_runner.py
```

Expected: all `test_runner.py` tests pass; Ruff reports `All checks passed!`.

- [ ] **Step 7: Stage only Task 2 hunks and commit**

```powershell
git add -p -- src/deep_research/evaluation/runner.py tests/test_evaluation/test_runner.py
git diff --cached --check
git diff --cached
git commit -m "feat: define evaluation status summary feedback"
```

Expected staged diff: only the imports, four new tests, `_quality_thresholds`, `evaluation_failure_reason`, and `build_evaluation_summary_feedback`. Pre-existing URL, focused-example, tracker, and provider-routing hunks remain untouched unless already committed separately.

---

### Task 3: Run the Summary Evaluator After All Row Evaluators

**Files:**
- Modify: `tests/evaluation_fakes.py:95-136`
- Modify: `src/deep_research/evaluation/runner.py:468-538,692-923`
- Modify: `tests/test_evaluation/test_runner.py:167-480`

**Interfaces:**
- Consumes:
  - `build_evaluation_summary_feedback(...)` from Task 2.
  - `repetitions_by_case`, populated only by `_dispatch_judge` after code and judge feedback for one row are complete.
  - LangSmith's synchronous summary signature `(runs: Sequence[Any], examples: Sequence[Any]) -> EvaluationResults`.
- Produces:
  - `_build_case_results(case_by_identity: Mapping[tuple[str, int], EvaluationCase], repetitions_by_case: Mapping[tuple[str, int], Sequence[RepetitionResult]], *, runtime: EvaluationRuntimeConfig) -> list[CaseResult]`.
  - A closure named `evaluation_status` passed as the sole item in `summary_evaluators`.
  - `FakeEvaluateRunner.summary_feedback: list[dict[str, Any]]`, populated only after every row evaluator has run.
  - Separate `evaluation_errors`, `auxiliary_errors`, and `summary_errors` lists. Only `evaluation_errors` participates in `decide_status`; all three are persisted in `ExperimentResult.errors`.

- [ ] **Step 1: Write the failing fake-lifecycle and runner-wiring tests**

Add these tests to `test_runner.py`:

```python
@pytest.mark.asyncio
async def test_run_agent_evaluation_passes_one_named_summary_evaluator(
    settings, runtime_config_for, tmp_path, evaluation_harness
) -> None:
    runner = FakeEvaluateRunner(examples=evaluation_harness.examples)

    await run_agent_evaluation(
        settings,
        runtime_config_for("planner"),
        cases=evaluation_harness.cases,
        evaluate=runner,
        **evaluation_harness.kwargs(tmp_path),
    )

    summaries = runner.calls[0]["summary_evaluators"]
    assert len(summaries) == 1
    assert summaries[0].__name__ == "evaluation_status"


@pytest.mark.asyncio
async def test_the_summary_observes_every_completed_row_before_it_runs(
    settings, runtime_config_for, tmp_path, partially_failing_harness
) -> None:
    runner = FakeEvaluateRunner(examples=partially_failing_harness.examples)

    result = await run_agent_evaluation(
        settings,
        runtime_config_for("planner"),
        cases=partially_failing_harness.cases,
        evaluate=runner,
        **partially_failing_harness.kwargs(tmp_path),
    )

    assert len(runner.rows) == 9
    assert _summary_values({"results": runner.summary_feedback}) == {
        "evaluation_status": result.status,
        "evaluation_failure_reason": (
            "focused-decomposition repetition 2 failed provider_failure"
        ),
    }
```

- [ ] **Step 2: Run the two tests to verify red**

Run:

```powershell
python -c "import sys; sys.path.insert(0, r'src'); import pytest; raise SystemExit(pytest.main(['tests/test_evaluation/test_runner.py','-k','named_summary_evaluator or observes_every_completed_row','-q']))"
```

Expected: the first test fails because `summary_evaluators` is absent; the second fails because `FakeEvaluateRunner` has no `summary_feedback`.

- [ ] **Step 3: Make the offline fake reproduce LangSmith summary timing**

In `FakeEvaluateRunner.__init__`, add:

```python
self.summary_feedback: list[dict[str, Any]] = []
```

In `FakeEvaluateRunner.__call__`, keep parallel `runs` and `example_rows` lists. Append each `FakeRun` and its `FakeExampleRow` after the row evaluators complete. After both repetition/example loops finish, invoke summary evaluators synchronously and flatten their result:

```python
summary_evaluators = kwargs.get("summary_evaluators") or []
runs: list[FakeRun] = []
example_rows: list[FakeExampleRow] = []

# Existing repetition/example loops remain here. Reuse one `example_row`
# for the row evaluators, then append only after all row evaluators finish:
runs.append(run)
example_rows.append(example_row)

for evaluator in summary_evaluators:
    try:
        result = evaluator(runs, example_rows)
        feedback = list(result.get("results", []))
        self.summary_feedback.extend(feedback)
    except Exception:
        # LangSmith 0.10.11 logs and swallows summary-evaluator failures.
        continue
```

The actual edit must retain the existing selected-data behavior, evaluator await handling, row recording, and `FakeExperimentResults` URL. The summary loop belongs after all rows and before the return.

- [ ] **Step 4: Extract one case-result builder used by summary and local result**

Add `_build_case_results` after `build_case_result` in `runner.py`:

```python
def _build_case_results(
    case_by_identity: Mapping[tuple[str, int], EvaluationCase],
    repetitions_by_case: Mapping[
        tuple[str, int], Sequence[RepetitionResult]
    ],
    *,
    runtime: EvaluationRuntimeConfig,
) -> list[CaseResult]:
    threshold, floor = _quality_thresholds(runtime)
    return [
        build_case_result(
            case_by_identity[identity],
            repetitions_by_case[identity],
            threshold=threshold,
            floor=floor,
        )
        for identity in case_by_identity
        if repetitions_by_case[identity]
    ]
```

Replace the inline threshold/floor/case-results block at the bottom of `run_agent_evaluation` with a call to this helper. This preserves registry insertion order and the existing exclusion of identities with no repetition result.

- [ ] **Step 5: Build and pass the post-row summary closure**

Retain Task 1's `evaluation_errors` and `auxiliary_errors`, then add the summary-specific list before calling `evaluate`:

```python
evaluation_errors: list[EvaluationFailure] = []
auxiliary_errors: list[EvaluationFailure] = []
summary_errors: list[EvaluationFailure] = []
```

Add the synchronous closure after `_dispatch_judge.__name__`:

```python
def _evaluation_status_summary(
    runs: Sequence[Any], examples: Sequence[Any]
) -> dict[str, list[dict[str, JsonValue]]]:
    del runs, examples
    try:
        case_results = _build_case_results(
            case_by_identity,
            repetitions_by_case,
            runtime=runtime,
        )
        return build_evaluation_summary_feedback(
            case_results,
            tier=runtime.tier,
            runtime=runtime,
            errors=evaluation_errors,
        )
    except Exception as error:
        summary_errors.append(
            EvaluationFailure(
                stage="trace",
                reason="langsmith_summary_unavailable",
                message=_safe_error_message(error, secrets),
                exception_type=type(error).__name__,
            )
        )
        return {"results": []}


_evaluation_status_summary.__name__ = "evaluation_status"
```

Pass it to `evaluate`:

```python
summary_evaluators=[_evaluation_status_summary],
```

Rename the existing `errors` list to `evaluation_errors`. When `evaluate` raises, append the existing `langsmith_unavailable` failure to `evaluation_errors`. After `evaluate` returns or raises, build local cases through `_build_case_results` and construct the local result as follows:

```python
status=decide_status(
    case_results,
    tier=runtime.tier,
    runtime=runtime,
    errors=evaluation_errors,
),
errors=[*evaluation_errors, *auxiliary_errors, *summary_errors],
```

This split is mandatory: a summary failure is visible in the artifact but cannot turn a genuine `FAILED` or `REVIEW REQUIRED` quality verdict into `INFRASTRUCTURE FAILURE`.

- [ ] **Step 6: Run the timing and runner regression tests**

Run:

```powershell
python -c "import sys; sys.path.insert(0, r'src'); import pytest; raise SystemExit(pytest.main(['tests/test_evaluation/test_runner.py','-q']))"
```

Expected: all runner tests pass, including the existing threshold wiring, partial-failure continuation, transport-failure, experiment URL, focused-data, and artifact revalidation tests. The new timing test reports 9 rows and matching local/LangSmith status.

- [ ] **Step 7: Prove local artifacts and row metrics remain unchanged**

Extend `test_the_artifact_is_written_and_revalidates` with:

```python
assert set(_summary_values({"results": runner.summary_feedback})) == {
    "evaluation_status",
    "evaluation_failure_reason",
}
assert all(
    "evaluation_status" not in repr(row["feedback"])
    and "evaluation_failure_reason" not in repr(row["feedback"])
    for row in runner.rows
)
```

This proves the new values are summary feedback rather than duplicated row feedback; the existing `restored == result` assertion continues to prove the artifact contract is unchanged.

- [ ] **Step 8: Run Ruff and commit only Task 3 hunks**

```powershell
python -m ruff check src/deep_research/evaluation/runner.py tests/evaluation_fakes.py tests/test_evaluation/test_runner.py
git add -p -- src/deep_research/evaluation/runner.py tests/evaluation_fakes.py tests/test_evaluation/test_runner.py
git diff --cached --check
git diff --cached
git commit -m "feat: publish evaluation status summary to LangSmith"
```

Expected: Ruff passes. The staged diff contains the fake summary lifecycle, shared case builder, summary closure, `summary_evaluators` argument, three-way error-list split, and Task 3 tests only.

---

### Task 4: Observe SDK-Swallowed Summary Upload Failures

**Files:**
- Modify: `src/deep_research/evaluation/runner.py:692-923,994-1070`
- Modify: `src/deep_research/evaluation/cli.py:277-346`
- Modify: `tests/evaluation_fakes.py:29-136`
- Modify: `tests/test_evaluation/test_runner.py:415-480`
- Modify: `tests/test_evaluation/conftest.py:2472-2505`
- Modify: `tests/test_evaluation/test_suite.py:1-120`

**Interfaces:**
- Consumes:
  - LangSmith's summary upload call shape: `client.create_feedback(run_id=None, project_id=<experiment id>, key=..., value=...)`.
  - `_safe_error_message(error, secrets)` for redaction and fail-closed secret checking.
- Produces:
  - `_SummaryFeedbackClient(client: Any, *, errors: list[EvaluationFailure], secrets: Sequence[str])`, a transparent proxy that delegates all attributes and intercepts only failures from the two project-feedback keys.
  - `run_agent_evaluation(..., langsmith_client: Any | None = None) -> ExperimentResult`; when supplied, pass the proxy as `client=` to `evaluate`.
  - `run_suite_evaluation(..., langsmith_client_factory: Callable[[], Any] = LangSmithClient, ...) -> SuiteResult`.
  - `FakeLangSmithClient(..., project_feedback_error: Exception | None = None)`.

- [ ] **Step 1: Write the failing verdict-neutral upload-failure test**

Extend `FakeLangSmithClient`'s constructor interface in the test first, then add this test to `test_runner.py`:

```python
@pytest.mark.asyncio
async def test_summary_upload_failure_is_recorded_without_rewriting_verdict(
    settings, runtime_config_for, tmp_path, partially_failing_harness
) -> None:
    secret = "sk-summary-upload-secret-123456"
    client = FakeLangSmithClient(
        project_feedback_error=ConnectionError(
            f"summary upload rejected credential {secret}"
        )
    )
    runner = FakeEvaluateRunner(examples=partially_failing_harness.examples)

    result = await run_agent_evaluation(
        settings,
        runtime_config_for("planner"),
        cases=partially_failing_harness.cases,
        evaluate=runner,
        langsmith_client=client,
        secrets=(secret,),
        **{
            key: value
            for key, value in partially_failing_harness.kwargs(tmp_path).items()
            if key != "secrets"
        },
    )

    assert result.status == "FAILED"
    assert any(
        error.stage == "trace"
        and error.reason == "langsmith_summary_unavailable"
        for error in result.errors
    )
    assert secret not in result.model_dump_json()
```

Import `FakeLangSmithClient` alongside `FakeEvaluateRunner`.

- [ ] **Step 2: Run the test to verify red**

Run:

```powershell
python -c "import sys; sys.path.insert(0, r'src'); import pytest; raise SystemExit(pytest.main(['tests/test_evaluation/test_runner.py','-k','summary_upload_failure','-q']))"
```

Expected: failure because `FakeLangSmithClient` does not accept `project_feedback_error` and `run_agent_evaluation` does not accept `langsmith_client`.

- [ ] **Step 3: Make the fakes emulate SDK feedback submission and swallowing**

Change `FakeLangSmithClient.__init__` to accept and store the optional error:

```python
def __init__(
    self,
    *,
    datasets: Sequence[FakeDataset] = (),
    project_feedback_error: Exception | None = None,
) -> None:
    self._project_feedback_error = project_feedback_error
    # Keep all existing initialization unchanged.
```

Update its `create_feedback` double:

```python
def create_feedback(self, run_id, key, **kwargs: Any) -> None:
    if (
        run_id is None
        and kwargs.get("project_id") is not None
        and key in {"evaluation_status", "evaluation_failure_reason"}
        and self._project_feedback_error is not None
    ):
        raise self._project_feedback_error
    self.feedback.append({"run_id": run_id, "key": key, **kwargs})
```

In the `FakeEvaluateRunner` summary loop from Task 3, after recording `feedback`, submit each item through the injected client and keep the `try/except` around the whole evaluator, matching LangSmith 0.10.11:

```python
client = kwargs.get("client")
if client is not None:
    for item in feedback:
        client.create_feedback(
            run_id=None,
            project_id="experiment-1",
            **item,
        )
```

When `create_feedback` raises, `FakeEvaluateRunner` must swallow it. The production proxy, not the fake runner, is responsible for recording the error.

- [ ] **Step 4: Implement the transparent project-feedback client proxy**

Add these constants and the private proxy next to `EvaluateCallable` in `runner.py`:

```python
_SUMMARY_FEEDBACK_KEYS = frozenset(
    {"evaluation_status", "evaluation_failure_reason"}
)


class _SummaryFeedbackClient:
    def __init__(
        self,
        client: Any,
        *,
        errors: list[EvaluationFailure],
        secrets: Sequence[str],
    ) -> None:
        self._client = client
        self._errors = errors
        self._secrets = tuple(secrets)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._client, name)

    def create_feedback(self, *args: Any, **kwargs: Any) -> Any:
        try:
            return self._client.create_feedback(*args, **kwargs)
        except Exception as error:
            key = kwargs.get("key")
            run_id = kwargs.get("run_id")
            project_id = kwargs.get("project_id")
            if (
                key in _SUMMARY_FEEDBACK_KEYS
                and run_id is None
                and project_id is not None
            ):
                self._errors.append(
                    EvaluationFailure(
                        stage="trace",
                        reason="langsmith_summary_unavailable",
                        message=_safe_error_message(error, self._secrets),
                        exception_type=type(error).__name__,
                    )
                )
            raise
```

The proxy must re-raise so SDK behavior remains unchanged. It must not record row-feedback failures, must not suppress the SDK's own logging, and must not expose the wrapped client's credentials.

- [ ] **Step 5: Pass the proxy through `run_agent_evaluation`**

Add the optional keyword parameter:

```python
langsmith_client: Any | None = None,
```

Immediately before the `evaluate` call, create:

```python
evaluation_client = (
    _SummaryFeedbackClient(
        langsmith_client,
        errors=summary_errors,
        secrets=secrets,
    )
    if langsmith_client is not None
    else None
)
```

Pass `client=evaluation_client` to `evaluate`. Keep `auxiliary_errors` and `summary_errors` out of `decide_status`, as established in Tasks 1 and 3. Do not read back feedback from LangSmith and do not inspect `AsyncExperimentResults._summary_results`.

- [ ] **Step 6: Run the upload-failure and secret tests**

Run:

```powershell
python -c "import sys; sys.path.insert(0, r'src'); import pytest; raise SystemExit(pytest.main(['tests/test_evaluation/test_runner.py','tests/test_evaluation/test_isolation_and_secrets.py','-q']))"
```

Expected: all tests pass. The new test keeps local status `FAILED`, persists one safe `langsmith_summary_unavailable` error, and contains no secret.

- [ ] **Step 7: Wire the configured client through agent and suite entry points**

In `cli._run_agent_pipeline`, pass the already-created client:

```python
langsmith_client=langsmith_client,
```

Import `Client` with the existing `aevaluate` import in `runner.py`:

```python
from langsmith import Client as LangSmithClient
from langsmith.evaluation import aevaluate
```

Add this keyword-only parameter to `run_suite_evaluation`:

```python
langsmith_client_factory: Callable[[], Any] = LangSmithClient,
```

Inside each agent's existing `try` block, construct one client and pass it to `run_agent_evaluation`:

```python
langsmith_client = langsmith_client_factory()

# Existing run_agent_evaluation call:
langsmith_client=langsmith_client,
```

Keeping construction inside the per-agent `try` preserves suite isolation: one client-construction failure becomes that agent's infrastructure result and does not abort the remaining agents.

- [ ] **Step 8: Inject fake suite clients and test all six summary calls**

In `tests/test_evaluation/conftest.py`, import `FakeLangSmithClient` and add this entry to `_suite_factory_kwargs()`:

```python
langsmith_client_factory=FakeLangSmithClient,
```

Add this test to `test_suite.py`:

```python
@pytest.mark.asyncio
async def test_each_suite_experiment_publishes_status_summary_feedback(
    settings, tmp_path, suite_harness
) -> None:
    await run_suite_evaluation(settings, **suite_harness.kwargs(tmp_path))

    assert len(suite_harness.runner.calls) == 6
    assert all(call["client"] is not None for call in suite_harness.runner.calls)
    assert len(suite_harness.runner.summary_feedback) == 12
    assert {
        item["key"] for item in suite_harness.runner.summary_feedback
    } == {"evaluation_status", "evaluation_failure_reason"}
```

- [ ] **Step 9: Run all evaluation tests and Ruff**

Run:

```powershell
python -c "import sys; sys.path.insert(0, r'src'); import pytest; raise SystemExit(pytest.main(['tests/test_evaluation','-q']))"
python -m ruff check src/deep_research/evaluation/ tests/evaluation_fakes.py tests/test_evaluation/
```

Expected: all offline evaluation tests pass; Ruff reports `All checks passed!`.

- [ ] **Step 10: Stage only Task 4 hunks and commit**

```powershell
git add -p -- src/deep_research/evaluation/runner.py src/deep_research/evaluation/cli.py tests/evaluation_fakes.py tests/test_evaluation/conftest.py tests/test_evaluation/test_runner.py tests/test_evaluation/test_suite.py
git diff --cached --check
git diff --cached
git commit -m "fix: surface LangSmith summary submission failures"
```

Expected: the staged diff contains the client proxy, runner/client wiring, fake failure behavior, and Task 4 tests only. The planner-enablement changes are already isolated in Task 1's commit.

---

### Task 5: Full Offline Verification and Scope Audit

**Files:**
- Verify only: `src/deep_research/evaluation/runner.py`
- Verify only: `src/deep_research/evaluation/cli.py`
- Verify only: `tests/evaluation_fakes.py`
- Verify only: `tests/test_evaluation/`
- Verify only: `docs/superpowers/specs/2026-08-24-langsmith-evaluation-status-design.md`

**Interfaces:**
- Consumes: the four implementation commits above.
- Produces: evidence that the whole offline suite, Ruff, summary-feedback scope, secret exclusions, and Git scope are clean. No production or test edit is expected in this task; if verification reveals a defect, return to the owning task, add a reproducing test, fix it, rerun that task's checks, and make a scoped fix commit.

- [ ] **Step 1: Run the complete worktree-first offline test suite**

```powershell
python -c "import sys; sys.path.insert(0, r'src'); import pytest; raise SystemExit(pytest.main([]))"
```

Expected: every non-`live` test passes. Report the exact passed/skipped/warning counts from this run; do not reuse the planning baseline count.

- [ ] **Step 2: Run full Ruff checks**

```powershell
python -m ruff check src/ tests/
```

Expected: `All checks passed!`.

- [ ] **Step 3: Verify the two summary keys exist only in intended locations**

```powershell
rg -n "evaluation_status|evaluation_failure_reason" src tests
```

Expected production matches: constants/helper/summary closure in `runner.py` only. Expected test matches: runner, suite, and fake assertions only. There must be no match in row evaluators, judge payloads, target outputs, reporting, configuration, or model schemas.

- [ ] **Step 4: Verify unsafe fields are not read by failure-reason construction**

Inspect `evaluation_failure_reason` and confirm it references only:

```text
case.case_id
repetition.repetition
repetition.errors[0].reason
repetition.gates.failed_ids[0]
repetition.completed
repetition.judge.status
repetition.judge.not_run_reason
repetition.aggregate_quality
case.average_quality
case.passed
error.stage
error.reason
```

It must not reference `.message`, gate `.detail`, judge `.rationale`, run/example inputs, target output text, prompts, URLs, or exception text.

- [ ] **Step 5: Audit the final diff and repository state**

```powershell
git status --short
git log -4 --oneline
git diff --check
git diff --stat origin/feat/deepseek-evaluation-cutover...HEAD
```

Expected: the implementation history contains the four scoped commits after this plan commit. The planner-enablement commit contains the reviewed dirty baseline plus its hardening; the three status-summary commits remain independently reviewable. No provider, gate, threshold, artifact schema, endpoint, or project-name file is added to the feature diff.

- [ ] **Step 6: Record the implementation handoff evidence**

Report:

```text
Offline tests: <exact command and exact result>
Ruff: <exact command and exact result>
Live providers: Not run (intentionally out of scope)
LangSmith UI verification: Not run (requires a later approved live evaluation)
Coverage: Not measured
Commits: <four exact implementation commit hashes>
Remaining dirty files: <exact git status --short output, or clean>
```

Do not claim the feedback columns are visible in a live LangSmith experiment until a separately approved live run confirms them.

---

## Approved UI-Visibility Extension

Live verification confirmed that LangSmith stores the summary feedback, but
the current Experiments view does not render project-level feedback as a table
field. The same authoritative values must also be written to project metadata
for UI monitoring. This extension keeps summary feedback, quality verdicts,
gates, thresholds, exit codes, names, endpoint, row feedback, and artifact
schema unchanged.

### Task 6: Amend the design and implementation plan

**Files:**
- Modify: `docs/superpowers/specs/2026-08-24-langsmith-evaluation-status-design.md`
- Modify: `docs/superpowers/plans/2026-08-24-langsmith-evaluation-status.md`

- [x] Document the UI limitation, the second project-metadata publication
  channel, public `evaluation_results.experiment_id`, read–merge–update
  behavior, and `langsmith_project_metadata_unavailable` as a
  verdict-neutral observability reason.
- [x] Preserve the existing summary-feedback path and all existing evaluation
  contracts.
- [ ] Commit the document amendment after `git diff --check` and a placeholder
  scan.

### Task 7: Add failing tests and extend the offline LangSmith fakes

**Files:**
- Modify: `tests/test_evaluation/test_runner.py`
- Modify: `tests/evaluation_fakes.py`

- [ ] Add tests for one shared status-value builder, metadata merging that
  preserves unrelated keys, project-ID routing through `experiment_id`, and
  read/update/missing-ID failures that preserve the local verdict and redact
  secrets.
- [ ] Extend `FakeExperimentResults` with an optional `experiment_id` and
  `FakeLangSmithClient` with fake projects, `read_project`, `update_project`,
  call recording, and injectable read/update errors.
- [ ] Run this focused red test command before production changes:

```powershell
python -c "import sys; sys.path.insert(0, r'src'); import pytest; raise SystemExit(pytest.main(['tests/test_evaluation/test_runner.py','-k','status_values or status_metadata or project_metadata','-q']))"
```

Expected before implementation: failure because the fake project API and
production metadata publication do not yet exist. Commit the fake/test change
only after the fake setup is complete and the test remains red for the missing
runner behavior.

### Task 8: Publish status metadata after every successful evaluation

**Files:**
- Modify: `src/deep_research/evaluation/runner.py`

**Interfaces:**
- Add `build_evaluation_status_values(cases, *, tier, runtime, errors=()) ->
  dict[str, str]` and make `build_evaluation_summary_feedback` delegate to it.
- Add a pure merge helper that returns `{**(existing_metadata or {}),
  **status_values}` without mutating the input.
- Add a publication helper that calls `read_project(project_id=...)`, merges
  `project.metadata`, and calls `update_project(project_id=..., metadata=...)`.

- [ ] After `evaluate` returns, build final cases and status values from
  `evaluation_errors` only, obtain the public `evaluation_results.experiment_id`,
  and publish through the original injected LangSmith client.
- [ ] Record missing IDs and read/update errors as
  `EvaluationFailure(stage="trace", reason="langsmith_project_metadata_unavailable", ...)`
  in a separate error list. Exclude that list, summary errors, and URL errors
  from `decide_status`; include them only in `ExperimentResult.errors`.
- [ ] Keep local `metadata=experiment_metadata(runtime, settings)` unchanged.
- [ ] Run the runner tests and Ruff, then commit the production wiring.

### Task 9: Full offline verification and UI-contract audit

**Files:**
- Verify: `src/deep_research/evaluation/runner.py`
- Verify: `tests/evaluation_fakes.py`
- Verify: `tests/test_evaluation/test_runner.py`
- Verify: the amended design and plan documents

- [ ] Run:

```powershell
python -c "import sys; sys.path.insert(0, r'src'); import pytest; raise SystemExit(pytest.main([]))"
python -m ruff check src/ tests/
python -c "import inspect; from langsmith import Client; from langsmith.evaluation._runner import ExperimentResults; assert hasattr(ExperimentResults, 'experiment_id'); assert 'metadata' in inspect.signature(Client.update_project).parameters"
git diff --check
```

- [ ] Audit that the two status values are shared by feedback and metadata,
  unrelated metadata survives, no secret-bearing fields enter the reason, and
  no target output or row feedback schema changes.
- [ ] Record exact test/Ruff results, `Coverage: Not measured`, and the
  distinction between offline verification and the required new live UI check.
