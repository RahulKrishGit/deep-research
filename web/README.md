# Deep Research console (`web/`)

The Next.js 16 console for the FastAPI service. It renders `docs/design/` from the API through a
same-origin streaming proxy (`app/api/[...path]/route.ts`).

## Run

1. API — from the repository root, in one of two modes:
   - **replay** (free, offline; the real graph on scripted cases): `python -m deep_research.api --mode replay`
     (`--replay-case ID` and `--replay-delay-ms N` pick and pace the scripted case).
   - **live** (the default; needs the configured secrets and spends their credit):
     `python -m deep_research.api --mode live`.

   Both bind `127.0.0.1:8000` unless `--host`/`--port` say otherwise.
2. App — `npm install` once, then `npm run dev` → http://localhost:3000. It binds 127.0.0.1 only: the
   API binds loopback on purpose and the proxy would otherwise undo that. `DEEP_RESEARCH_API_URL`
   overrides the API origin (default `http://127.0.0.1:8000`). The topbar shows a muted "replay mode"
   chip when the API answers `X-Deep-Research-Mode: replay`.

## Scripts

| Script | What it does |
| --- | --- |
| `npm run dev` / `build` / `start` | Next.js dev server, production build, production server (all on 127.0.0.1). |
| `npm run typecheck` | `tsc --noEmit`. |
| `npm test` | Vitest: the event core on recorded replay captures, the display helpers, the SSE reader, the API client, the proxy and the components. |
| `npm run test:e2e` | `next build`, then Playwright (chromium project) against its own servers: the API in replay mode on 8010, the app on 3010, and a second app on 3011 pointed at a closed port. Run `npx playwright install chromium` once. Inside a `.worktrees/*` tree set `DEEP_RESEARCH_PYTHON` to the venv interpreter (`…/deep-research/.venv/Scripts/python.exe`). |
| `npm run capture:visual` | Playwright `visual` project: full-page screenshots of the stages and views into `visual/<VISUAL_CHECKPOINT>/` (default `C4`; git-ignored). |
| `npm run capture:events -- <case-id>` | Records a replay session's frames into `test/fixtures/events/` (needs the API in replay mode with `--replay-delay-ms 0` at `DEEP_RESEARCH_API_URL`, default `http://127.0.0.1:8010`). After a re-capture, rewrite the page's active-row pin from the new frames: `WRITE_ACTIVE_ROWS=1 npx vitest run test/active-row.test.ts`. |
| `npm run check:css` | `app/globals.css` begins with the prototype's CSS (`docs/design/prototype/index.html` lines 7-1137) verbatim; anything after it sits under the app-only header. |

## Replay mode

The proxy forwards the replay server's request headers, so a test or capture can steer it:

- `X-Replay-Clarify: on` on `POST /research` makes the one-time check ask a fixed set of three
  questions (Region, Period, For). Without it the replay asks nothing. `e2e/clarify.spec.ts` uses it.
- `X-Replay-Hold-After: <event type>[#<n>]` holds the stream after that event until the session is
  stopped, so a step's live brief can be inspected. `e2e/progress.spec.ts` and the step-brief captures
  use it.

Replay runs the engine ahead of its paced stream, which shapes what the API can show:

- A note added on the replay server is acknowledged but never applied: `/status` reads it
  `not_checked`, and the report has no "Your note" row.
- By the time a step can be stopped the engine has usually finished, so the session ends `stopped`
  with no report on the API (its files land in the replay's temporary directory, deleted on exit).
- `duration_seconds` is the unpaced span (about 0.2 s), so the report head bar reads `0m 00s`; a
  dropped finding is labelled `X01` and not-found targets read `topic-01-target-01`. All three are
  the engine's own values.

## OneDrive

The tree lives under OneDrive. If `npm install` or `next build` fails with `EPERM`/`EBUSY`, pause
syncing for the command; if it persists, junction `node_modules` and `.next` to a directory outside
OneDrive (`mklink /J`).
