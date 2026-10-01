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
- `npm run capture:visual` — the twenty-seven full-page captures (13 stages/views × 1252 and
  390 px, and the report's cards at 1920 px) into `visual/<VISUAL_CHECKPOINT>/` (default `C4`).
- `npm run capture:events -- <case-id>` — records a replay session's frames into
  `test/fixtures/events/` (needs the API in replay mode with `--replay-delay-ms 0` at
  `DEEP_RESEARCH_API_URL`, default `http://127.0.0.1:8010`).
- `npm run check:css` — `app/globals.css` begins with the prototype's CSS, verbatim.

## Notes

- The one-time check (live-briefs spec §4.4-§4.5): the composer sends
  `ask_clarifying_questions` (the settings row "Ask me when the question is unclear", on by
  default). In replay mode the check asks nothing unless `POST /research` carries
  `X-Replay-Clarify: on` — the proxy forwards it — and then asks a fixed set of three
  questions (Region, Period, For); `e2e/clarify.spec.ts` and the `10-clarify` capture use it.
- Reader notes (live-briefs spec §4.6-§4.7): the note line at the foot of the running
  pipeline card posts `POST /research/{id}/notes` (the proxy forwards it); replay mode reads
  each note with a scripted interpreter that keeps it as written. Replay runs the engine
  ahead of its paced stream, so a note added on the replay server is acknowledged but never
  applied: `/status` reads it `not_checked`, and the report's bottom line has no line for it
  (notes-progress-report spec §7.2); `e2e/notes.spec.ts` and the `11-note-ack` and
  `12-report-notes` captures use it.
- The report (notes-progress-report spec §7.6): one card per section, with a contents list
  that is a sticky rail left of the cards from a 1310px report stage and a sticky row of
  chips above them below that; `e2e/report-layout.spec.ts` and the `18-report-cards` and
  `18b-report-cards-1920` captures use it.
- Stop (notes-progress-report spec §8): the topbar's Stop, beside the running chip, asks once and
  posts `POST /research/{id}/stop` (the proxy forwards it); the session ends `stopped` and its page
  keeps the pipeline frozen where it stopped, with "Ask again". On the replay server the engine runs
  ahead of its paced stream, so by the time a step can be stopped it has usually finished — its
  files land in the replay's temporary directory, which the server deletes on exit — but the session
  itself still ends `stopped`, with no report on the API. `e2e/stop.spec.ts` and the
  `19-stop-confirm` and `20-stopped` captures use it.
- Replay mode's `duration_seconds` is the unpaced span (about 0.2 s), so the report head
  bar reads `0m 00s` there; a dropped finding is labelled `X01`; not-found targets read
  `topic-01-target-01`. All three are the engine's own values.
- The tree lives under OneDrive. If `npm install` or `next build` fails with `EPERM`/`EBUSY`,
  pause syncing for the command; if it persists, junction `node_modules` and `.next` to a
  directory outside OneDrive (`mklink /J`).
