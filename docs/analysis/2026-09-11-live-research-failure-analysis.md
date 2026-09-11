# Live Research Failure Analysis

Status: adjudicated evidence report and fix plan. This commit changes documentation only; the proposed application fixes are not implemented here.

Date: 2026-09-11
Repository: `RahulKrishGit/deep-research`
Branch: `codex/live-ui-demo`
Code baseline: `f7fba3a` (`origin/main` at worktree creation)
Report revision: `5ac6b5e` before this documentation update

## Executive assessment

The demonstration exposed four separate conditions. They must not be collapsed into one root cause:

1. A confirmed environment-isolation failure selected an editable installation from another worktree. This explains the earlier missing-UI-module test collection failures and is not an application defect.
2. The Streamlit UI and research worker can initialize concurrently. The observed NumPy partial-initialization exception is consistent with a timing/import-order interaction, but the exact causal race was not independently reproduced. A concrete avoidable trigger is the string form of `run_every="2s"`: Streamlit's duration parser imports NumPy and Pandas, while numeric seconds do not use that path.
3. The live provider path failed in several core stages. The evidence establishes provider-backed failures, but not whether their underlying cause was transport, rate limiting, an HTTP rejection, structured-output failure, an output limit, account state, or another provider condition.
4. A confirmed application-level status defect allowed a controlled but materially degraded run to be labeled `Completed`. The graph can preserve a limitation-only fallback report and still map Critic termination to `completed`, even when essential researcher, fact-checker, or synthesizer provider errors are present.

The highest correctness priority is the fourth condition: a safe partial artifact is useful, but it must not be presented as a successfully completed research answer.

## Scope and evidence method

This report analyzes the Streamlit UI failure and the incomplete live research session from the isolated worktree. It uses:

- the implementation and tests at the code baseline;
- the sanitized live-run observations recorded below;
- a read-only review of the exact source and committed report at report revision `5ac6b5e` using the dedicated GPT-5.6 Sol/High browser review.

The review did not modify the repository, create a pull request, or execute commands from the browser review. It did not use or request environment-file contents, credentials, raw provider payloads, or raw logs.

## Verified execution context

- Worktree: `C:\Users\Rahul Krishnamoorthy\OneDrive\Documents\Python Scripts\deep-research\.worktrees\live-ui-demo`
- Branch: `codex/live-ui-demo`, tracking `origin/codex/live-ui-demo`
- The original `main` checkout had pre-existing uncommitted edits and was left untouched.
- The worktree was based on the fetched `origin/main` tip `f7fba3a`.
- The main checkout's `.env` was loaded into the Streamlit child process only. No secret values were copied into the worktree or recorded here.
- Streamlit was served at `http://localhost:8501`.
- The isolated worktree's `src` path was selected at process scope after the global editable install resolved `deep_research` to another linked worktree.

## Baseline verification

After installing the development dependencies, the full offline suite passed when the isolated source path was selected explicitly:

```text
2119 passed, 2 skipped, 1 deselected, 2 warnings
```

This is evidence for that revision and environment only. It is not a substitute for a fresh verification run after implementing the proposed fixes.

## Live run under investigation

Question:

> What are the biggest developments in renewable energy storage in 2025–2026, and how might they affect grid reliability?

Session: `c2e6e3d2546744b89b36f6308646f032`

Observed session metadata:

- UI terminal label: `Completed`
- Iterations: 3 of 3
- Planned subtopics: 6
- Trace: `https://eu.smith.langchain.com/o/dec92fa1-b347-483c-8a51-9cd727ee089c/projects/p/5186a642-2114-4f70-9931-a5e6dcdcef7a/r/01a09169-6d4f-7060-840a-1764441e4cbf?poll=true`
- Final report: `output/report-c2e6e3d2546744b89b36f6308646f032-3.md`
- Recorded token totals: 152,385 input and 92,627 output

The generated report retained citations and explicit limitations, but it contained no findings or verified claims. Recorded error categories included:

- researcher finding-extraction provider failure;
- skipped researcher sub-topics after the extraction failure;
- no sources for source evaluation;
- no findings for fact checking;
- fact-check claim-extraction provider failure;
- no evidence for synthesis;
- synthesizer report-generation provider failure; and
- one document-reader tool failure followed by continuation.

The report therefore could not answer the grid-reliability question or provide quantified conclusions. The UI displayed the errors and limitations, but the top-level `Completed` label was misleading for this result.

## Timeline and observed symptoms

1. The worktree was created from fetched `origin/main`, and development dependencies were installed.
2. An initial test invocation resolved the globally installed editable `deep_research` package from the unrelated `cross-agent-planner-fix-parity` worktree. UI tests then failed during collection because that package did not contain the expected UI modules.
3. Selecting the isolated worktree's `src` path at process scope restored the expected package and the full offline suite passed.
4. The first live Streamlit progress refresh raised:

   `ImportError: cannot import name 'NDArray' from partially initialized module 'numpy._typing'`

   The stack entered the `@st.fragment(run_every="2s")` path in `src/deep_research/ui/app.py`, Streamlit's `time_to_seconds`, and NumPy while the research worker was starting.
5. Fresh-process NumPy imports passed repeatedly, including importing Streamlit before NumPy. Simple isolated concurrent-import probes also passed. This makes a clean package-install failure less likely and leaves the exact timing-sensitive interaction unconfirmed.
6. Restarting Streamlit with the isolated source path and NumPy preloaded allowed the UI to load and the completed session to be reopened. No application source was changed for that workaround.
7. The live run completed its configured graph iterations, but provider-backed researcher, fact-checker, and synthesizer operations failed. The resulting fallback artifact was limitation-only and evidence-empty.

## Findings and confidence

| Finding | Classification | Confidence | Evidence-based assessment |
| --- | --- | --- | --- |
| A different worktree's editable `deep-research` package was selected | Environment/setup cause for the import and collection problem | High | The imported package origin pointed at another worktree, and explicit selection of the isolated `src` path corrected the problem. Do not describe this as an application defect. |
| Worker runtime initialization and Streamlit fragment setup can overlap | Architectural contributing condition | High | `LocalResearchController.start()` launches a background thread immediately; the subsequent Streamlit rerun renders the active session and its live-progress fragment. |
| The NumPy exception was caused by that overlap | Probable but unconfirmed causal hypothesis | Medium | The timing and preload workaround are consistent with it, but independent concurrent probes did not reproduce the exception. |
| `run_every="2s"` enters Streamlit's NumPy/Pandas duration parser | Concrete avoidable trigger | High | The reviewed Streamlit implementation imports NumPy and Pandas for the string-duration path; numeric seconds bypass that path. |
| A broken NumPy version or an incident-specific dependency pin is the root cause | Unsupported hypothesis | Low | Fresh NumPy probes passed, the exact environment version set was not captured in the original evidence, and the project currently uses broad lower bounds. Do not pin NumPy solely because of this incident. |
| Model-provider calls failed in several core agents | External/provider operational failure | High | The live evidence establishes the failures, but not their underlying transport, HTTP, quota, rate-limit, structured-output, output-limit, or account cause. |
| Missing API-key presence caused the provider failures | Unlikely but not fully excluded | Medium | Strict startup checks presence, but presence does not prove validity, authorization, quota, or service health. |
| Provider retries are absent | False | High | The provider layer already owns bounded retries for typed transient timeout, rate-limit, transport, and retryable HTTP errors; the reviewed default is two retries after the initial attempt. |
| Provider failures can still lead to graph status `completed` | Confirmed application semantics defect | High | Agent provider errors can be recorded as non-recoverable without halting the graph; Critic routing can map `critique_satisfied` directly to `completed`. Existing tests intentionally preserve this behavior. |
| The document-reader failure caused the overall run failure | Not supported | High | That tool failure was followed by continuation, matching the intended recoverable tool-failure behavior. |
| Global dependency drift is a reproducibility risk | Contributing setup risk | Medium | Lower-bound dependencies and reusable global editable installs make future reproduction less deterministic. This is not proof of the live incident's cause. |

## Root-cause analysis

### 1. Worktree and interpreter contamination (confirmed environment issue)

The repository expects an editable install, but later use of bare executables allows the shell to select a different Python environment or a globally installed editable package. In this run, the package imported by tests came from another worktree. This created a false application failure: the isolated checkout's UI modules were not the modules being tested.

The durable contract should be one worktree, one virtual environment, and one editable install. Every Python entry point should be launched through the interpreter belonging to that environment. `PYTHONPATH` was a useful diagnostic correction for this run, but it should not be the normal setup because it can mask a contaminated interpreter.

### 2. Streamlit/NumPy startup interaction (condition confirmed, exact cause unconfirmed)

The architecture allows overlap: `LocalResearchController.start()` starts a daemon worker thread, while a following Streamlit rerun renders the active session and sets up or refreshes the live-progress fragment. Runtime construction also contains lazy scientific dependencies; the local embedding implementation delays Chroma's `DefaultEmbeddingFunction` import until it is needed.

The UI currently passes `run_every="2s"` to `st.fragment`. The reviewed Streamlit time-conversion path imports NumPy and Pandas for a string interval. A numeric interval is returned directly. That means the smallest, safest mitigation is:

```python
@st.fragment(run_every=2.0)
```

This keeps the same two-second refresh behavior and removes an unnecessary NumPy/Pandas import from fragment scheduling. It is a targeted mitigation, not proof that the worker race was the sole cause. Do not add a permanent NumPy preload or dependency pin without reproducing the failure with the numeric interval and capturing the actual Python and dependency versions.

The remaining gap is a repeatable startup/refresh stress test that runs without network access and exercises the worker start together with the live fragment.

### 3. Provider degradation (confirmed operational symptom, underlying cause unknown)

Multiple core model-backed stages failed in the same run. Existing per-request retry behavior is already bounded and should remain owned by the provider layer. Adding generic retry loops around the Researcher, Fact Checker, or Synthesizer would duplicate requests, multiply cost, and potentially worsen rate limiting.

The current bounded-degradation behavior has useful properties:

- the Researcher preserves findings collected before an extraction failure and stops additional sub-topics after an exhausted provider path;
- the Synthesizer produces a deterministic fallback Markdown artifact from retained evidence;
- the zero-evidence case is handled explicitly instead of asking the model to invent content; and
- recoverable tool failures can be recorded while work continues.

These behaviors should be preserved. The missing policy is session-level provider degradation: after an essential provider operation exhausts its retry policy, later macro refinement should not make more provider calls that cannot improve the retained artifact. Deterministic cleanup and partial-report assembly may continue, but the terminal result must be `incomplete`.

### 4. Terminal-status semantics (confirmed application defect)

The system currently conflates two questions:

- Did execution reach a safe, controlled terminal point?
- Did the research produce a result that can legitimately be called complete?

The graph has a small set of graph-owned errors that produce `failed`, while agent-level errors—including errors marked `recoverable=False`—normally do not halt the graph. The Critic can set `should_continue=False`; `critique_satisfied` then maps directly to `completed`. A limitation-only report can therefore coexist with a `completed` status.

The fix should not halt on every provider failure. It should separate execution termination from research completeness:

- `failed`: graph/runtime failure prevented controlled termination;
- `incomplete`: controlled termination produced a partial or evidence-insufficient result, or an essential research-stage failure occurred;
- `completed`: substantive evidence and the required quality gates are present, with no quality-blocking provider failure;
- `max_iterations`: the configured refinement budget ended a still-valid run, with the existing product semantics preserved; and
- `cancelled`: a future explicit cooperative-cancellation outcome, or a controlled `incomplete` reason until a public status is added.

Do not define completeness as `state.errors` being empty or as every `recoverable=False` error being terminal. Recoverable tool issues may coexist with a valid result, and the existing `recoverable` field has both agent-level and graph-level meanings. Add an explicit project-owned quality predicate or enumerated set for material failures, including as applicable:

- researcher extraction provider failure;
- source-evaluator scoring provider failure when scoring is required for the result;
- fact-check claim extraction or verification provider failure;
- synthesizer report provider failure; and
- Critic review provider failure.

An evidence-empty synthesis (`synthesizer_no_evidence`) must also make a run ineligible for `completed`, even if a fallback Markdown file was written. The partial report should remain readable and downloadable, but the UI, CLI, API, and history consumers must receive the same semantic status.

## What should be fixed

### P0 — Correct result status

Change terminal classification in `src/deep_research/graph/state.py` or a small shared outcome-quality helper, then update presentation and consumers as needed in:

- `src/deep_research/graph/state.py`;
- `src/deep_research/graph/orchestrator.py`;
- `src/deep_research/runtime/outcome.py`;
- `src/deep_research/ui/runner.py` and `src/deep_research/ui/components.py`;
- `src/deep_research/cli.py`; and
- API/history projections if they consume the terminal label.

Required behavior:

- the observed combination of core provider failures and an evidence-empty fallback report ends `incomplete`;
- a retained fallback artifact remains available;
- `completed` is impossible when an enumerated quality-blocking error exists; and
- a recoverable isolated tool error does not automatically invalidate an otherwise complete result.

### P0 — Remove the avoidable fragment import path

Change `@st.fragment(run_every="2s")` in `src/deep_research/ui/app.py` to `@st.fragment(run_every=2.0)`. Add a focused test or source-level assertion if the current UI harness can observe the decorator configuration. Do not make NumPy preload part of the application contract.

### P0 — Make worktree/interpreter isolation executable

Update `README.md` and the live-verification documentation to use the current interpreter for all entry points:

```text
python -m pip install -e ".[dev]"
python -m pytest
python -m streamlit run src/deep_research/ui/app.py
```

Add a fail-fast preflight for manual verification that confirms `deep_research` resolves inside the current checkout and that Streamlit is launched by the same interpreter. A safe boolean such as `package_origin_matches_checkout` is preferable to persisting developer-specific absolute paths. Document `PYTHONPATH` as a diagnostic-only fallback.

### P1 — Add safe provider-failure diagnostics

Extend provider/agent error projections and observability with structured, redacted fields:

- provider name and effective model alias;
- agent and operation, such as `react_decision`, `researcher_extraction`, `fact_check_claim_extraction`, `fact_check_verification`, `synthesis`, or `critic_review`;
- stable failure category;
- retryable flag;
- configured retry count and attempts consumed;
- whether structured-output repair was attempted; and
- a safe HTTP status or category when available.

Never record prompts, completions, rejected model output, API keys, authorization headers, raw exception strings, or raw provider response bodies. Record safe dependency versions separately—Python, Streamlit, NumPy, Pandas, ChromaDB, OpenAI SDK, Pydantic, and LangGraph—and include only a local-only origin check or boolean in durable reports.

### P1 — Add session-level provider degradation

Add explicit state/routing policy rather than parsing error strings. After an essential provider outage exhausts its retry policy:

- preserve already-collected evidence;
- complete deterministic/local cleanup and safe artifact assembly;
- skip unnecessary refinement passes and provider calls that cannot improve the artifact; and
- end `incomplete`, not `completed`.

Test this with scripted exhausted provider failures across multiple nodes and macro iterations.

### P1 — Add startup/refresh stress coverage

Add a network-free regression under `tests/test_ui/` using Streamlit `AppTest`, a subprocess harness, or the closest stable project harness. Repeatedly start the worker and exercise live refresh. The test must fail if the partial NumPy import error appears, and it must not require a live provider.

### P1 — Add cooperative cancellation

Cancellation did not cause this incident and should not be called a root cause. It is important for paid live runs because the current daemon-thread controller has no supported cancellation path. Add a controller-owned cancellation signal and check it between graph nodes, ReAct iterations, and before starting another provider/tool call. Do not attempt to kill a Python thread. Preserve partial state and use a dedicated `cancelled` status when contract changes permit it; otherwise use a controlled `incomplete` reason initially.

### P2 — Improve reproducibility and live smoke coverage

- Add a deliberate constraints/lock strategy after compatibility verification; do not add an incident-specific NumPy pin without a reproducer or an explicit compatibility policy.
- Keep the existing bounded, opt-in DeepSeek structured-output smoke test as the first paid/provider check.
- Add a deliberately small, opt-in end-to-end live research smoke path covering provider, search, graph, and synthesis before a full UI demonstration.

## Regression and acceptance criteria

The implementation work is ready to review when all of the following are true:

- A fixture reproducing the observed provider-error/evidence-empty state ends `incomplete` and still exposes the fallback report.
- A valid result with only a permitted recoverable tool error can still reach `completed` when all substantive completeness gates pass.
- `run_every` remains two seconds but is numeric, and the UI no longer relies on a NumPy preload to start.
- Repeated worker-plus-fragment startup does not produce a partial NumPy import failure in the offline harness.
- A contaminated package origin is detected before manual/live verification, and the documented launch uses `python -m streamlit` from the worktree environment.
- Provider failures expose safe stage/category/attempt metadata without secrets or raw provider payloads.
- An exhausted essential provider outage does not trigger unnecessary refinement calls and ends `incomplete` while preserving partial evidence.
- User cancellation prevents later provider/tool calls at the next safe boundary and leaves a controlled, inspectable outcome.
- The full offline suite is rerun after changes; live-provider checks remain explicit and opt-in.

## Clean verification runbook

Keep deterministic/offline verification strictly separate from anything that spends money or depends on an external service.

1. Create a dedicated virtual environment for the worktree.
2. Install and launch with the same interpreter:

   ```text
   python -m pip install -e ".[dev]"
   python -m pytest
   python -m streamlit run src/deep_research/ui/app.py
   ```

3. Before manual verification, confirm that `deep_research.__file__` resolves beneath the current checkout and that `python -m streamlit` belongs to the same environment. Record only safe dependency versions and package-origin booleans.
4. Run offline tests first. Launch the deterministic mock UI at `tests/test_ui/manual_mock_app.py` and inspect running, completed, incomplete/partial, max-iteration, and failed states.
5. Exercise the real UI without starting research. Repeat startup/initial rendering enough times to cover the prior timing window and confirm that no import or fragment-startup error appears.
6. End the offline phase. For external checks, verify only the names/presence of required environment variables, never their values.
7. Run the bounded opt-in provider adapter smoke test (`tests/live/test_deepseek_live.py`) first. If it fails, inspect the safe provider category and retry metadata before spending money on an end-to-end UI run. Do not treat an adapter failure as a UI failure.
8. Run one bounded end-to-end session with maximum macro iterations of 1 and a focused question. Capture only the session ID, terminal status, counts, safe error categories, and trace URL if tracing is enabled.
9. Apply the result gate: `completed` requires substantive evidence and no quality-blocking provider failure; provider degradation must be `incomplete` with any partial report retained.
10. Only after that bounded check succeeds, run the full demonstration question. Treat live-provider behavior as manual, nondeterministic verification rather than a deterministic merge gate.

## Corrections to the preliminary report

The preliminary evidence log was directionally correct but needed these qualifications:

- Describe the NumPy event as consistent with a timing/import-order interaction, not as a proven race.
- Add the concrete `run_every="2s"` string-parser mechanism and test `run_every=2.0` as the smallest mitigation.
- Describe the `PYTHONPATH` workaround as process-scoped diagnosis, not the recommended setup.
- State that bounded provider retries already exist; the missing pieces are safe failure classification, session-level degradation policy, and correct terminal semantics.
- Qualify the live UI label: the graph reached a controlled terminal point, but the research answer was incomplete. Do not call the run simply “completed.”
- Explicitly document the contradiction between agent-level provider-error semantics and graph-level status mapping.
- Distinguish safe fallback artifact generation from successful research.
- Do not assign the shared provider failure to a specific upstream cause without raw, sanitized category metadata.
- Retain the conclusion that dependency pinning is not justified by this event alone.

## Evidence boundaries

- No API keys, `.env` contents, raw provider payloads, authorization headers, or raw logs are included.
- The live run is evidence of behavior on 2026-09-11, not proof that the provider or dependency environment is permanently unavailable.
- The NumPy causal mechanism remains a hypothesis until a deterministic reproducer or stronger runtime evidence exists.
- The proposed fixes and tests above are recommendations; this report commit does not implement them.
- The baseline test result applies to the tested revision/environment only and must be refreshed after implementation.
