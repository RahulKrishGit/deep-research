# Dark live-render fix report

## Scope

- Worktree: `C:/Users/Rahul Krishnamoorthy/OneDrive/Documents/Python Scripts/deep-research/.worktrees/streamlit-ui-polish`
- Branch: `codex/streamlit-ui-polish`
- Base commit: `af235299e3d16efd157dc016e86fe49c5e5d296b`
- Task: route the existing global `STATIC_CSS` plus `REPORT_CSS` through Streamlit's non-iframed HTML boundary so the automatic dark media rule survives.

## Root-cause and compatibility evidence

The reproduced live evidence showed that the current `st.markdown(..., unsafe_allow_html=True)` path emitted the ordinary CSS rules but dropped `@media (prefers-color-scheme: dark)` before the stylesheet reached the page. The failing regression captured the same boundary defect: the current app produced no `st.html` call for the merged global CSS payload.

The installed Streamlit runtime is `1.63.0`. Its local `st.html` implementation documents that HTML is not iframed and that style-only content is sent through the event container, which is the global-style path needed here. The repository's declared `streamlit>=1.49` floor is preserved; no dependency or token changes were made.

## TDD evidence

### RED

Command:

`python -m pytest tests/test_ui/test_app.py -q -k global_css`

Result before implementation: **1 failed, 114 deselected in 2.07s**. The expected failure was `test_global_css_uses_non_iframed_html_injection`; `captured` was empty because production still called `st.markdown`.

### GREEN

Command:

`python -m pytest tests/test_ui/test_app.py -q -k global_css`

Result after implementation: **1 passed, 114 deselected in 1.84s**.

The regression captures the exact merged `STATIC_CSS.replace("</style>", f"{REPORT_CSS}</style>", 1)` payload through `st.html` and asserts that `@media (prefers-color-scheme: dark)` remains present.

## Implementation

- Replaced only the global CSS injection call in `src/deep_research/ui/app.py`: `st.markdown(..., unsafe_allow_html=True)` is now `st.html(...)`.
- Preserved the existing `STATIC_CSS` plus `REPORT_CSS` merge, buttons, routing, callbacks, report markup, and all light/dark token values.
- Added the deterministic AppTest regression in `tests/test_ui/test_app.py`.

## Verification

Command:

`python -m pytest tests/test_ui -q`

Result: **229 passed, 2 skipped in 37.76s**.

Command:

`python -m pytest -q`

Result: **2113 passed, 2 skipped, 1 deselected, 2 warnings in 65.35s (0:01:05)**. The warnings are the existing LangSmith `ast.Str` deprecation and Starlette/httpx deprecation.

Command:

`python -m ruff check .`

Result: **All checks passed!**

Command:

`git diff --check`

Result: **exit code 0** with no whitespace errors. Git emitted only LF-to-CRLF normalization warnings for the two modified source/test files.

## Live-render status

The injection-path hypothesis is verified by the failing/green boundary regression and the local Streamlit implementation evidence. Browser acceptance was not run in this turn because browser use was explicitly prohibited. The controller must verify after the commit that dark preference produces dark app surfaces and that light preference retains the Editorial light palette.

## Changed files

- `src/deep_research/ui/app.py`
- `tests/test_ui/test_app.py`
- `.superpowers/sdd/2026-09-10-streamlit-ui-theme-buttons/dark-live-fix-report.md`

## Concerns

- Browser light/dark acceptance remains pending controller verification.
- No backend, session, controller, provider, workflow, button, report-markup, dependency, or token changes were made.
