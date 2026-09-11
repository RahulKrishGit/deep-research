# Streamlit UI Theme and Button States Design

## Context

The exact 8511 browser render is in a dark browser preference, but the
Streamlit UI forces a light canvas, sidebar, header, and control palette. The
result is a mixed light/dark shell: the document body is dark while the app is
`#FCFCFA`, and the deterministic harness's `Demo state` select is dark against
the light shell. The existing button wiring is functional, but its visual
states depend too much on Streamlit defaults and do not form a coherent
contract across themes.

## Decision

Keep the approved Editorial Research Canvas light palette as the default and
add an automatic dark palette selected only by `prefers-color-scheme: dark`.
Do not add a manual theme toggle or session-state theme key. Theme selection is
pure CSS so it cannot change research state or trigger a workflow rerun.

The dark palette uses a genuinely dark canvas (`#0E1117`) and elevated surface
`#171C22`) with light text, muted text, borders, teal actions, and dark
semantic tints. Every theme-sensitive rule consumes semantic CSS variables;
header/chrome, fields, read-only Markdown, selected rows, status surfaces,
links, focus rings, and buttons must all follow the active palette.

Primary, secondary, link, and disabled button states get explicit project-owned
normal, hover, pressed, focus, and disabled styling. Existing action handlers,
keys, navigation, research submission, session behavior, and native Streamlit
controls remain unchanged.

The running subtopic keeps its semantic `dr-subtopic-row--running` class and
moves its active tint, radius, and padding from inline HTML into CSS so the
dark palette can override it.

## Verification

Add static CSS and AppTest regressions before implementation, observe them fail,
then implement the smallest token/component changes. Run the focused tests,
complete UI suite, complete repository suite, Ruff, and `git diff --check`.
Visually inspect light and dark preferences at 1280x720, 768x1024, and 390x844
using production New/Starting screens and deterministic harness states. Exercise
New Research, Open, Session history, Start Research, iteration controls,
History search/filter/Open, detail disclosures, LangSmith, native Deploy/menu,
and Starting disabled controls. Confirm no clipping, overflow, unreadable
disabled state, or theme-mismatched surface.

## Scope boundaries

No changes to the controller, runner, history persistence, providers, polling,
session lifecycle, research workflow, dependencies, JavaScript, external
assets, generated Emotion selectors, or manual theme controls.
