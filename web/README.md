# Deep Research console (`web/`)

The Next.js 16 console for the FastAPI service. It renders `docs/design/` from the live
API through a same-origin streaming proxy (`app/api/[...path]/route.ts`).

## Run

1. API: from the repository root, `python -m deep_research.api --mode replay` (free, offline)
   or `--mode live` (needs the configured secrets and spends their credit). Default `127.0.0.1:8000`.
2. App: `npm install` once, then `npm run dev` → http://localhost:3000 (bound to
   127.0.0.1 only — the API binds loopback on purpose and the proxy would
   otherwise undo that).
   `DEEP_RESEARCH_API_URL` overrides the API origin (default `http://127.0.0.1:8000`).

## Test

- `npm test` — Vitest: the event core on real replay captures, the display helpers, the
  SSE reader, the client, the proxy, the components.
- `npm run test:e2e` — `next build` then Playwright against the API in replay mode
  (three servers: the API on 8010, the app on 3010, an app on 3011 pointed at a closed
  port). Inside a `.worktrees/*` tree set `DEEP_RESEARCH_PYTHON` to the venv interpreter
  (`…/deep-research/.venv/Scripts/python.exe`); `npx playwright install chromium` once.
- `npm run capture:visual` — the fourteen full-page captures (7 stages/views × 1252 and
  390 px) into `visual/<VISUAL_CHECKPOINT>/` (default `C4`).
- `npm run capture:events -- <case-id>` — records a replay session's frames into
  `test/fixtures/events/` (needs the API in replay mode with `--replay-delay-ms 0` at
  `DEEP_RESEARCH_API_URL`, default `http://127.0.0.1:8010`).
- `npm run check:css` — `app/globals.css` begins with the prototype's CSS, verbatim.

## Notes

- Replay mode's `duration_seconds` is the unpaced span (about 0.2 s), so the report head
  bar reads `0m 00s` there; a dropped finding is labelled `X01`; not-found targets read
  `topic-01-target-01`. All three are the engine's own values.
- The tree lives under OneDrive. If `npm install` or `next build` fails with `EPERM`/`EBUSY`,
  pause syncing for the command; if it persists, junction `node_modules` and `.next` to a
  directory outside OneDrive (`mklink /J`).
