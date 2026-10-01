# Progress, report and Stop picks (2026-09-30)

A copy of the Claude Design canvas the owner picked from
(https://claude.ai/artifact/FmPfLZmGCk5wuAamVu8suT, private). The canvas's working source lives in
the git-ignored `.superpowers/progress-canvas/project/`; this folder is the committed copy the plans'
`.superpowers/progress-canvas/project/…` references resolve to on any other machine.

Each `.dc.html` file is one artboard holding several options side by side. Only the picked option
is binding; where an artboard and the spec
(`docs/superpowers/specs/2026-09-30-notes-progress-report-stop-design.md`) differ, **the spec wins**.

| File | Step | Picked |
|---|---|---|
| `Main.dc.html` | Planning | option **B** (topic slots fill, then tick) |
| `Evaluating.dc.html` | Evaluating sources | option **A** (count, bar, strong / fair / weak) |
| `Verifying.dc.html` | Verifying evidence | option **C** ("Just checked" ticker) |
| `Writing.dc.html` | Writing report | option **E** ("Just written" ticker, ✓ backed / ✗ removed) |
| `Reviewing.dc.html` | Reviewing | **A, revised** (✓/✗ per criterion, no scores; A1 all met, A2 sent back). The spec shows five criteria (D23), not the artboard's seven |
| `Report.dc.html` | Report page | **cards + contents list** (chips on phone) |
| `Stop.dc.html` | Stop | the three states shown |

`progress.css` holds the token-only rules the artboards add on top of `theme.css` (a copy of
`docs/design/running-stage-picks/theme.css`). `canvas.json` is the canvas index.
