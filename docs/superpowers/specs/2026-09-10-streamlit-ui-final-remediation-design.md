# Streamlit UI Final Remediation Design

Date: 2026-09-10
Scope: close the remaining Sol High re-review findings on
`codex/streamlit-ui` without changing the research engine, provider
integration, tracing, or branch-review boundary.

## Design

Sidebar lifecycle truth is session-ID aware. Each visible recent-session row
owns a status placeholder in a session-keyed mapping. The live progress
fragment keeps the selected session as its main-canvas target, then refreshes
the selected row from its supplied snapshot and every other visible row from
that session's controller snapshot/history entry. A session's status cannot be
written into another row's placeholder.

Starting is a two-phase Streamlit state. Form submission stores a normalized
question and iteration limit as a pending request, marks the start as in
flight, and reruns. The next render shows the Starting cue with the question
and iteration controls disabled. That render consumes the pending request,
starts the controller once, clears the pending state on success or failure,
and transitions to the existing Running or safe-error route.

The completed details rail adds a presenter-only credibility distribution
sentence derived from the four persisted counts. The deterministic helper
handles no evaluated sources, all-unrated sources, dominant tiers, and mixed
distributions without making a quality claim unsupported by the counts.

The implementation plan and package metadata use `streamlit>=1.49`, the
minimum selected for the keyed and gapped `st.container` contract. Primary
buttons receive an explicit `min-height: 44px` rule, covered by a CSS test and
checked in the rendered local harness.

## Testing

Add offline regression tests for:

- independent sidebar status updates for two concurrently running sessions;
- pending-start persistence, disabled Starting controls, and one start call;
- zero, mixed, dominant, and all-unrated credibility distributions;
- plan/package version consistency; and
- the 44px primary-button CSS contract.

Run the focused red-green tests, the complete UI suite, the repository suite,
Ruff, and `git diff --check`. Render the deterministic mock app through the
in-app browser for New, Running, Completed, History, and the 44px button
measurement. Do not invoke a live provider or perform a branch-wide review.
