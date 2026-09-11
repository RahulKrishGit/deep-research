# Live Research Failure Analysis

Status: evidence capture prepared for Sol/High review. This file will be updated with the adjudicated root causes, fix plan, and verification plan.

Date: 2026-09-11
Repository: `RahulKrishGit/deep-research`
Branch: `codex/live-ui-demo`
Code baseline: `f7fba3a` (`origin/main` at worktree creation)

## Scope

Analyze the observed Streamlit UI failure and the incomplete live research run from the isolated worktree, identify root causes, separate environment issues from repository defects, and propose concrete repository changes with tests and verification steps.

## Verified execution context

- A linked worktree was created at `C:\Users\Rahul Krishnamoorthy\OneDrive\Documents\Python Scripts\deep-research\.worktrees\live-ui-demo`.
- The original `main` checkout had pre-existing uncommitted edits and was left untouched.
- The worktree was based on the fetched `origin/main` tip `f7fba3a`.
- The main checkout's `.env` was loaded into the Streamlit child process only; no secret values were copied into the worktree or recorded here.
- Streamlit was served at `http://localhost:8501`.
- The isolated worktree's `src` path was explicitly selected at process scope after the global editable install resolved `deep_research` to another linked worktree.

## Baseline verification

The full offline test suite passed against the isolated worktree with its `src` path selected explicitly:

```text
2119 passed, 2 skipped, 1 deselected, 2 warnings
```

## Live run under investigation

Question:

> What are the biggest developments in renewable energy storage in 2025–2026, and how might they affect grid reliability?

Session: `c2e6e3d2546744b89b36f6308646f032`

Observed terminal state:

- UI status: `Completed`
- Iterations: 3 of 3
- Planned subtopics: 6
- Trace: `https://eu.smith.langchain.com/o/dec92fa1-b347-483c-8a51-9cd727ee089c/projects/p/5186a642-2114-4f70-9931-a5e6dcdcef7a/r/01a09169-6d4f-7060-840a-1764441e4cbf?poll=true`
- Final report: `output/report-c2e6e3d2546744b89b36f6308646f032-3.md`

The report contained citations and limitations but no findings or verified claims. Recorded errors included provider failures during researcher finding extraction, fact-check claim extraction, and synthesizer report writing. The app surfaced these as execution errors and limitations rather than presenting unsupported findings.

## Initial observed symptoms

1. On the first live progress refresh, Streamlit raised:

   `ImportError: cannot import name 'NDArray' from partially initialized module 'numpy._typing'`

   The stack entered `@st.fragment(run_every="2s")` in `src/deep_research/ui/app.py`, then Streamlit's `time_to_seconds`, then NumPy import. The research worker was concurrently initializing the runtime.

2. Fresh-process NumPy imports passed repeatedly, including importing Streamlit before NumPy. A concurrent import probe also passed in isolation. This makes a clean package installation failure less likely and points to a timing-sensitive interaction in the real Streamlit-plus-worker startup sequence.

3. A later test invocation initially resolved `deep_research` from the unrelated linked worktree `cross-agent-planner-fix-parity`, causing six UI test collection errors. Running with the isolated worktree's `src` directory on `PYTHONPATH` restored the expected package and the full suite passed.

4. Restarting Streamlit with the isolated `src` path and NumPy preloaded allowed the UI to load and the completed session to be reopened without another UI import error.

## Review questions for Sol/High

- What is the most likely root cause of the Streamlit fragment/NumPy import failure, and is the correct fix application-level, dependency-level, or launch-environment-level?
- Which package-install or import-path assumptions make a fresh worktree resolve another worktree's editable package?
- Why did provider-backed researcher, fact-checker, and synthesizer calls fail while search and source citations were recorded?
- Which failure is masking the others, and what minimal diagnostics or structured error propagation should be added?
- What repository changes, regression tests, launch documentation, and live-provider verification should be proposed?

## Evidence boundaries

- No API keys, `.env` contents, raw provider payloads, or credentials are included.
- The local run is evidence of behavior on 2026-09-11, not proof that the provider or dependency environment is permanently unavailable.
- The initial evidence does not yet justify changing application code or dependency pins; root cause needs adjudication first.

