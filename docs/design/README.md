# Front-end design package

The design for the Deep Research console: a React/Next front end for the
existing FastAPI interface, for one local operator, with no authentication.

## Provenance

Exported from the **Open Design** desktop app, project
`ce1a3b11-67b8-4877-a1f8-ed3ab7ca52b9`. The author froze the design on
2026-09-16 (last source edit 19:14:37 local). `open-design/` below is a
complete copy of that project; nothing from it is omitted.

## Authoritative artifacts

These are the current, correct versions. Read these.

| Path | What it is |
|---|---|
| `DESIGN.md` | Screen inventory, status mapping, motion spec, and the decisions the design system does not answer |
| `api-gaps.md` | What each stage needs that the FastAPI surface does not serve |
| `prototype/index.html` | The reference prototype. Self-contained: 3,067 lines, one `<style>` block, one `<script>` block, zero external references. The only literal colour values live in the `:root` token block (lines 12–45), which is where the Perplexity AI design system is captured |
| `prototype/states.html` | Static fixture of the edge states |
| `reference/*.png` | Reference renders, regenerated from the frozen prototype and asserted against the stage they claim to show |

The design was built against a **1252 × 853** viewport (`DESIGN.md` §7).

## `open-design/` — the complete original project

Everything here is a faithful copy for provenance. Where it disagrees with
the table above, the table wins.

| Path | What it is | Status |
|---|---|---|
| `artifacts/*.artifact.json` | Open Design's export manifests for the two prototypes | Original metadata |
| `captures/*.png` | 13 captures made in the app (8 `screenshot-*`, 5 `drawing-*`) | **Superseded** — see below |
| `skills/web-prototype-022bee5593/` | The Open Design skill, templates and layout references that produced the prototype | Original scaffolding |
| `version-history/*.html` | 68 autosave snapshots plus `manifest.json` | **History only** |

**Why `captures/` is superseded.** All 13 are 1252×853 renders of the same
prototype — including the five named `drawing-*`, which are app captures
rather than hand-drawn artwork. Every one is older than the final edit to
`prototype/index.html` (newest capture 18:41, prototype last edited 19:14),
so none of them show the current design. They are kept because they record
the design's evolution and may cover states `reference/` does not.

**Why `version-history/` is history only.** It is Open Design's own autosave
trail. Snapshot `0068` is byte-identical in size to `prototype/index.html`
(174,839 B), confirming the copied prototype is the newest version. Git now
supersedes this trail.

## Regenerating the reference renders

`reference/` is generated, not hand-made. To rebuild it:

```
node scripts/render_design_reference.mjs docs/design/prototype docs/design/reference
```

It drives the prototype over the Chrome DevTools Protocol using the page's own
`window.drConsole` review hook, and asserts the stage each capture landed on
before saving — so a render cannot silently depict the wrong screen. No npm
dependencies; it needs Google Chrome and Node 18+.

## Known defect in this package

`api-gaps.md` says the console has **four** stages (idle, running, report,
failed). `DESIGN.md` §3 says **five** (idle, submitted, running, report,
failed), and §7 confirms the prototype implements five. `api-gaps.md` is the
stale document, and its stage numbering is consequently off by one from
stage 2 onward: its "Stage 2 — Running" is DESIGN.md's stage 3. Re-key the
gaps to `DESIGN.md` §3 when implementing the API work.
