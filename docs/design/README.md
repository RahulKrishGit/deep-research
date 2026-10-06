# Front-end design package

The design for the Deep Research console: a React/Next front end for the
existing FastAPI interface, for one local operator, with no authentication.
The Next.js app in [`web/`](../../web/README.md) implements it; this package is
the reference it is built against.

## Contents

| Path | What it is |
|---|---|
| [`DESIGN.md`](DESIGN.md) | Screen inventory, status mapping, motion spec, and the decisions the design system does not answer |
| [`api-gaps.md`](api-gaps.md) | What each stage needs that the FastAPI surface does not serve |
| `prototype/index.html` | The reference prototype. Self-contained: one `<style>` block, one `<script>` block, no external stylesheets, scripts or fonts. The only literal colour values live in the `:root` token block at the top of the style block, which is where the Perplexity AI design system is captured |
| `prototype/states.html` | Static fixture of the edge states, with the status-mapping table rendered as live chips |

The design was built against a **1252 × 853** viewport (`DESIGN.md` §7).

The backend behaviour the design depends on is described in
[`docs/ARCHITECTURE.md`](../ARCHITECTURE.md) and the FastAPI interface in the
[root README](../../README.md#fastapi-interface).
