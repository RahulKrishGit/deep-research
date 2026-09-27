# Front-end design package — Evidence Verifier workflow update — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Bring `docs/design/` (DESIGN.md, api-gaps.md, README.md, the two prototypes and the reference renders) from the 2026-09-16 fact-checker/critic/refine pipeline to the Evidence Verifier pipeline this branch runs — in place, keeping theme, tokens, element positions and the battery-storage example.

**Architecture:** One self-contained prototype (`prototype/index.html`) is edited by hand in dependency order: fixtures and statuses first, then the composer, then the scripted stream, then the running-stage state machine (a pure `EVENT_HANDLERS` table driven by `applyEvent(run, ev)`, so the same code paints the running stage, the failed stage and a 100-event replay identically), then the two arcs, the failed stage, the report stage, the Evidence view and `states.html`; the render script gains two captures; the three documents are rewritten last, against the finished prototype. There is no unit-test suite, so every task's "failing test" is an executable check that fails before the edit and passes after: a throwaway Chrome-over-CDP probe driving `window.drConsole` (the render script's own mechanism), the spec's stale-name `rg` command scoped to the task's file, a Node key-set check fed by an offline replay of two scripted cases, and `git show`-based byte-identity checks for the frozen regions.

**Tech Stack:** Hand-written HTML/CSS/vanilla JS (no build step); Node v24.13.1 + Chrome 153.0.8010.53 headless over the DevTools Protocol (`scripts/render_design_reference.mjs` and the throwaway probe); ripgrep 15.1.0 (a builtin of the agent shell); git; the project venv's Python 3.12 (`.venv/Scripts/python.exe`) for the offline replay only. Windows.

**Spec:** `docs/superpowers/specs/2026-09-26-frontend-design-workflow-update-design.md` (committed at `61e9e43`, re-anchored at `b581b6d` after the rebase onto `origin/main` `2010c1d`; §2 decisions Q1–Q6 and §2.1 rulings R1–R4 are closed — do not reopen them). Read it alongside this plan; section numbers below (§3.3, §4.1, …) are the spec's unless prefixed "DESIGN.md".

## Global Constraints

- Worktree: `C:/Users/Rahul Krishnamoorthy/OneDrive/Documents/Python Scripts/deep-research/.worktrees/frontend-console`, branch `feat/frontend-console`. Every path below is relative to that root; every command runs from it. Commit after every task. Tasks run **one after another**, never two at once: they share one worktree and one `.git/index.lock`.
- In scope: `docs/design/DESIGN.md`, `docs/design/api-gaps.md`, `docs/design/README.md`, `docs/design/prototype/index.html`, `docs/design/prototype/states.html`, `docs/design/reference/*.png`, `scripts/render_design_reference.mjs`. Out of scope: product code, API code, `config.yaml`, the Next app, `docs/design/open-design/` (must stay byte-identical to `2010c1d`).
- Theme, tokens, element positions and the battery-storage example content are **kept**. The `:root{…}` block (`index.html:11-55` at `2010c1d`) is byte-identical after the edit (AC18); nothing is inserted above it.
- DESIGN.md sections listed as verbatim in spec §5 — `:210-277` (§3.1), `:459-490` popover placement, `:525-584` (§3.3), `:967-1093` (§5.1–5.3), `:1151-1360` (§5.5–5.6) — stay byte-identical to `2010c1d` except the `:482-484` phrase ("the effort control explaining why it is unavailable when thinking is off" → "the read-only effort line and its thinking-off text"); §3.6 changes only its first sentence (`:775-777`), the `:790` anecdote line and an added 0.80 note (AC17).
- Stage rows (`STAGES`), in order, `data-stage` = node name: `planner` Planning `1–10 sub-topics` · `researcher` Researching `search · scrape · read · memory` · `source_evaluator` Evaluating sources `authority · recency · relevance` · `evidence_verifier` Verifying evidence `snippet on page · context check` · `report_writer` Writing report `verified findings only · statement check` · `report_reviewer` Reviewing `7 dimensions · accept at mean 0.80` · `finalize_report` Publishing `report · evidence log · quality record` (AC2).
- Composer: three model buttons `deepseek-flash` (default) · `deepseek-v4-flash` · `deepseek-v4-pro`; thinking `enabled` | `disabled`; effort is a read-only line `#effortLine` reading `effort per agent: planner max · reviewer max · others high`, or `effort: not sent (thinking disabled)`; **Extra passes** stepper `#stepExtra` with `EXTRA_MIN = 0`, `EXTRA_MAX = 2`, `EXTRA_DEFAULT = 1`, minus disabled at 0, plus at 2; `#segEffort` removed; the popover carries no other copy (R4). Request body exactly `{"query": "…", "max_iterations": 1, "output_format": "markdown", "config_overrides": {"llm": {"model": "deepseek-flash", "thinking_mode": "enabled"}, "output": {"directory": "output/"}}}`; `max_iterations` is always present, including `0`.
- Settings strip, five mono chips in this order on the submitted, running and report stages: `model deepseek-flash` · `thinking enabled` · `effort per agent` (or `effort not sent` when thinking is off) · `extra passes 1` · `out output/`. A chip's DOM is `<span class="opt"><span class="k">model</span>deepseek-flash</span>`, so its `textContent` has no space between label and value; checks join them with one space.
- Pass vocabulary: `pass p of P`, `p = iteration + 1` read from `graph.node.started.iteration` and `graph.extra_pass.started.iteration` only (never `researcher.tool_call.iteration`), `P = 1 + max_extra_passes` from `graph.session.started` (fallback: `session.passes` = 1 + the budget submitted); budget 0 reads `pass 1 of 1`. `passNumber()` stays the one place the zero-based offset lives.
- Chip notes: running `pass p of P`; `completed` → `review accepted · {score}` + not-found clause; `max_iterations` → `extra passes used` + clause; `incomplete` with `semantic_review_status == "scored"` → `not accepted · {score}` (R3); other `incomplete` → `review unavailable`; `failed` → `halted`. Clause: omitted at 0, `· 1 target not found`, `· {n} targets not found`. Scores print with two decimals.
- Arcs: extra pass `report_reviewer` → `researcher`, stroke `--warn` (`#f59e0b`), rows 2–6 reset; redraft `report_reviewer` → `report_writer`, stroke `--meta` (`#5c5c5e`), rows 5–6 reset. `flowing` on `graph.route.decided`, `settled` on the hop's own event, cleared on `graph.session.completed` or the next `graph.route.decided`. The reviewer's `graph.node.completed` is inert after a loop decision. `--meta` is a stroke only, never text; the loop tag's amber is `--status-warn`.
- Counters block (R1): inside the pipeline card, under the header, eyebrow `counted from the event stream`; rows `sub-topics researched` · `tool calls` (researcher only) · `findings` · `sources scored` · `verified / corrected / dropped` · `sentences / refused` · `review score`; an unreceived counter reads muted `not yet` (running) / `not reached` (failed), never `0`; the block's caption reads `pass p`. No layout change, no running-stage rail.
- Every unavailable value is muted text: `not yet`, `not reached`, `not measured`, `not scored`, `Not recorded`, `Not published`. Never `0`, `—`, `null`, a placeholder or a disabled control.
- Failed-stage headlines: `graph_planning_failed` → Planning failed; `graph_provider_configuration_error` → Model provider misconfigured; `graph_agent_configuration_error` → Agent misconfigured; `graph_invalid_agent_state` → Invalid agent state; `graph_invalid_route` → Invalid route; `graph_request_attempt_limit_exceeded` → Request attempt limit reached. Publishing is `skipped` by the client rule `graph.session.completed.status == "failed"` (never `has_report`).
- Fixture error types are the real ones only: `report_writer_statement_check_failed`, `evidence_verifier_context_check_failed`, `graph_provider_configuration_error`. Fixture evidence values are ones the engine can produce: `FigureAttribution` ∈ `own | relayed | unattributed`, `FigureKind` ∈ `actual | forecast` (`utils/types.py:324`, `:355`); a `quoted` or `dropped` finding carries no figures; the quality gate's hard-failure names are those of `agents/quality.py:142-144` (a disclosed missing target is not a hard failure: `hard_failures: []`).
- The stale-name command (spec §4.6 step 3) must print nothing over the five authoritative files (AC1); today it prints 39 / 2 / 0 / 59 / 6 lines. Kept text it must not match: `-webkit-line-clamp is the refinement` (DESIGN.md §3.1) and `Critical minerals` / `Critical Minerals` in the example.
- Viewports: `1252 × 853` desktop, `390 × 844` phone (the render script's `W/H`, `PHONE_W/PHONE_H`). Chrome: `C:/Program Files/Google/Chrome/Application/chrome.exe`, version read with PowerShell `(Get-Item 'C:/Program Files/Google/Chrome/Application/chrome.exe').VersionInfo.ProductVersion` (153.0.8010.53 today). Node 18+ (v24.13.1 installed). `node scripts/render_design_reference.mjs docs/design/prototype docs/design/reference` must end with `9/9 captures verified` (AC15).
- Never run the live CLI (`python -m deep_research …`), never call a model provider, never read `.env`. The only Python is the offline replay of §4.6 step 1, which is network-denied and scripted.
- Never name a model or provider-model id as a workflow role in docs or commits; `deepseek-flash` etc. appear only as product data (composer options, strip chips).
- Copy marked (I) in the spec is used as written, with one recorded exception: the loop-tag sentences pluralise honestly (`1 required target had no verified finding`, `Reviewer named 1 material defect`); at n ≥ 2 they read exactly as the spec writes them.
- Fixture questions: all seven fixtures ask battery-storage questions (spec §4.6 fixture table). `b41e77aa` — the session in the `08-evidence` capture — becomes `What is the levelised cost of grid-scale battery storage in 2025?` (controller decision, 2026-09-26); the other five existing questions are kept; the new `c3d7e5f1` asks `How do lithium iron phosphate and NMC cells compare for grid-scale storage?`. `How mature is quantum error correction?` stays a composer starter.

### Conventions every task uses

- **Shell.** Every shell block runs in the **agent's bash tool** from the worktree root: a POSIX shell in which `rg`, `node`, `git`, `wc`, `sed`, `awk`, `xxd` and quoted heredocs work as written. Two facts about this machine: `bash <file>` resolves to `C:\WINDOWS\system32\bash.exe` (WSL, no distribution) — so **no step runs a shell script by `bash file.sh`; checks are Node scripts run with `node`** — and real Git Bash (`C:/Program Files/Git/bin/bash.exe`) has no `rg`, so the checks never assume it. The venv interpreter runs by absolute path: `"C:/Users/Rahul Krishnamoorthy/OneDrive/Documents/Python Scripts/deep-research/.venv/Scripts/python.exe"`; the PowerShell form `& '…/python.exe'` is an equivalent alternative. Files in the worktree are CRLF on disk (`core.autocrlf=true`, LF in blobs); keep your editor on CRLF and let git normalise. Byte-identity checks below normalise `\r\n` → `\n` before comparing.
- **Throwaway files live outside the tree**, in `$TEMP/dr-probe/` (the agent shell exposes `%TEMP%` as `$TEMP`), including every Chrome profile the probe creates (`$TEMP/dr-probe/profiles/…`, deleted by the probe itself after each run). Nothing under `$TEMP` is ever `git add`ed. Task 14 deletes the directory.
- **The probe harness** (`$TEMP/dr-probe/probe.mjs`, written in Task 1 step 1) serves a prototype directory, launches headless Chrome, navigates, waits for `window.drConsole` (or `readyState` for `states.html`), evaluates one check file as the body of an `async` function and prints its JSON result; exit code 0 iff the result has `ok !== false`. It always tears Chrome down and removes its profile, even when the check throws. Predefined inside a check file: `c` (= `window.drConsole`), `$`, `$$`, `text(sel)` (whitespace-normalised `textContent`), `chipText(el)` (a strip chip's label and value joined by one space), `sleep(ms)`, `waitFor(fn, ms)`. Usage: `node "$TEMP/dr-probe/probe.mjs" docs/design/prototype index.html "$TEMP/dr-probe/<check>.js" [--viewport=WxH] [--reduced-motion]`.
- **Edits by anchor.** Line numbers quoted as `:N` are today's (`61e9e43`); each edit also quotes the first line of the region so it can be found after earlier edits moved it. In `index.html`, new CSS is appended at the end of the `<style>` block (before `</style>`, today `:1033`) so the `:root` block never moves.
- **Every step shows its final content.** There is no errata section: if a step here disagrees with an earlier draft of this plan, this text wins.

## Review Focus

1. **A late subscriber receives the whole run as one burst** (the real stream publishes a node's events together, and `GET /stream` replays 60+ events at once): after one synchronous `advanceTo` to the end of `8f2c1d90`'s script, the spine, header, tag, arc and counters must read exactly what per-node bursts produce — no `done` Reviewing above hollow rows, tag cleared, arc off, `↺` on Researching. → pinned in Task 4 step 12 (rows, counters) and Task 5 step 8 (arc, tag).
2. **Extra passes 0 must still send `max_iterations: 0`** — a `0` dropped as falsy would silently let the server default to 1 and buy an extra pass the operator declined. → pinned in Task 2 step 7.
3. **`evidence_counts: null` and `assessed_sources: 0` on the report stage** — no fixture opens the report stage with a null block (only the failed fixture is null), and a zero denominator would print `NaN` in the `Scored sources cited` meter. Both must read muted `not measured`. → pinned in Task 7 step 9.
4. **`max_iterations` with an empty `not_found_target_ids` list, and a list of two** — the clause must vanish at 0 and pluralise at 2 (`state.py:312-313` makes the empty list a real outcome). → pinned in Task 1 step 8.
5. **Switching between the two playback fixtures mid-run** — they share one `play` state; opening `c3d7e5f1` while `8f2c1d90` is settled on its extra-pass arc must start a clean run (no inherited arc, tag, counters or pass number), and reopening `8f2c1d90` must restart from Planning. → pinned in Task 4 step 13 (state) and Task 5 step 8 (arc, tag).

---

## File map

| File | Responsibility after this plan | Tasks |
|---|---|---|
| `docs/design/prototype/index.html` | the reference prototype: fixtures + statuses (T1), composer (T2), stage rows + scripted stream (T3), running-stage state machine, counters, `drConsole.advanceTo` (T4), two arcs, loop tag, `drConsole.arc/loop` (T5), failed stage (T6), report stage + rail (T7), Evidence view + `drConsole.view/evidence` (T8) | 1–8 |
| `docs/design/prototype/states.html` | static review fixture: chips, failed panel, partial outcomes, options-table frame | 9 |
| `scripts/render_design_reference.mjs` | nine asserted captures | 10 |
| `docs/design/reference/*.png` | regenerated renders 01–07, new 08, 09 | 10, 14 |
| `docs/design/DESIGN.md` | the design record, rewritten per spec §5: header through §3.6 (T11), §4 through §7 (T12) | 11, 12 |
| `docs/design/api-gaps.md`, `docs/design/README.md` | gaps re-keyed to five stages + sidebar; README revision note, line count, no "Known defect" | 13 |

---

### Task 1: Session fixtures, status table and topbar chip (spec §4.2, §4.6 fixture table)

**Files:**
- Modify: `docs/design/prototype/index.html:1450-1482` (`/* ═══ the client's own session ledger ═══ */` + `var SESSIONS = [`), `:1484-1519` (`var STATUS = {` through `statusNote`), `:1555-1558` (`function sessionSettings`)
- Test (throwaway, outside the tree): `$TEMP/dr-probe/probe.mjs`, `$TEMP/dr-probe/t1-chips.js`, `$TEMP/dr-probe/t1-clause.js`

**Interfaces:**
- Consumes: nothing new.
- Produces: `SESSIONS[]` entries `{ id, q, status, routeReason, iteration (zero-based), passes, started, finished, durationSeconds, reportPath, evidencePath, qualityPath, trace, group, playback?, script? ("extra_pass" | "redraft" | null), final? ({review, coverage, evidenceCounts}), review ({status, score} | null), coverage ({required_targets, answered_targets, missing_required_target_ids[], not_found_target_ids[]} | null), evidenceCounts (15 keys | null), errors[] }`; constants `BATTERY_COUNTS`, `COMPOSER_FINAL`, `STATEMENT_CHECK_ERROR`, `CONTEXT_CHECK_ERROR`; functions `passTotal(s) → number|null`, `passText(s) → string`, `fmtScore(v) → string|null`, `notFoundClause(s) → string`, `statusNote(s) → string`, `chipHTML(s)` (unchanged), `sessionSettings(session) → {model, thinking, outputDir, extraPasses}`.

- [ ] **Step 1: Write the throwaway probe harness (every later task reuses it)**

```bash
mkdir -p "$TEMP/dr-probe/profiles" && cat > "$TEMP/dr-probe/probe.mjs" <<'EOF'
// Throwaway: drive a prototype page over CDP and evaluate one async check file. Never committed.
// usage: node probe.mjs <protoDir> <file> <check.js> [--viewport=WxH] [--reduced-motion]
import { spawn, spawnSync } from 'node:child_process';
import { createServer } from 'node:http';
import { readFile, mkdtemp, mkdir, rm } from 'node:fs/promises';
import { existsSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import path from 'node:path';
const [PROTO_DIR, FILE, CHECK] = process.argv.slice(2, 5);
const flags = process.argv.slice(5);
const vp = (flags.find((f) => f.startsWith('--viewport=')) || '--viewport=1252x853').split('=')[1].split('x').map(Number);
const reduced = flags.includes('--reduced-motion');
const CHROME = process.env.CHROME || 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe';
const MIME = { '.html': 'text/html; charset=utf-8', '.css': 'text/css', '.js': 'text/javascript', '.png': 'image/png', '.svg': 'image/svg+xml' };
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const HERE = path.dirname(fileURLToPath(import.meta.url));
// read first, then answer: a missing file (Chrome asks for /favicon.ico) must be a clean 404, never a second header
const server = createServer(async (req, res) => {
  let file = '', buf = null;
  try {
    let rel = decodeURIComponent(new URL(req.url, 'http://x').pathname);
    if (rel === '/') rel = '/index.html';
    file = path.join(PROTO_DIR, rel);
    buf = await readFile(file);
  } catch { buf = null; }
  if (res.headersSent) return;
  if (buf === null) { res.writeHead(404); res.end('not found'); return; }
  res.writeHead(200, { 'content-type': MIME[path.extname(file)] || 'application/octet-stream' });
  res.end(buf);
});
let chrome = null, ws = null, profile = null;
let result = { ok: false, error: 'harness did not reach the check' };
try {
  await new Promise((r) => server.listen(0, '127.0.0.1', r));
  const origin = `http://127.0.0.1:${server.address().port}`;
  await mkdir(path.join(HERE, 'profiles'), { recursive: true });
  profile = await mkdtemp(path.join(HERE, 'profiles', 'chrome-'));
  chrome = spawn(CHROME, ['--headless=new', '--remote-debugging-port=0', `--user-data-dir=${profile}`, '--disable-gpu', '--no-first-run',
    '--no-default-browser-check', '--disable-extensions', '--hide-scrollbars', '--force-device-scale-factor=1', `--window-size=${vp[0]},${vp[1]}`, 'about:blank'],
    { stdio: 'ignore' });
  const portFile = path.join(profile, 'DevToolsActivePort');
  let devPort = null;
  for (let i = 0; i < 150 && !devPort; i++) {
    if (existsSync(portFile)) { const l = (await readFile(portFile, 'utf8')).split('\n')[0].trim(); if (l) devPort = l; }
    if (!devPort) await sleep(100);
  }
  if (!devPort) throw new Error('chrome never reported a devtools port');
  const target = (await (await fetch(`http://127.0.0.1:${devPort}/json/list`)).json()).find((t) => t.type === 'page');
  ws = new WebSocket(target.webSocketDebuggerUrl);
  await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });
  let seq = 0; const pending = new Map();
  ws.onmessage = (ev) => { const m = JSON.parse(ev.data); if (m.id && pending.has(m.id)) { const p = pending.get(m.id); pending.delete(m.id); m.error ? p.reject(new Error(m.error.message)) : p.resolve(m.result); } };
  const send = (method, params = {}) => new Promise((resolve, reject) => { const id = ++seq; pending.set(id, { resolve, reject }); ws.send(JSON.stringify({ id, method, params })); });
  const evaluate = async (expression) => {
    const r = await send('Runtime.evaluate', { expression, returnByValue: true, awaitPromise: true });
    if (r.exceptionDetails) throw new Error(`page threw: ${r.exceptionDetails.text} ${r.exceptionDetails.exception?.description || ''}`);
    return r.result.value;
  };
  await send('Page.enable'); await send('Runtime.enable');
  await send('Emulation.setDeviceMetricsOverride', { width: vp[0], height: vp[1], deviceScaleFactor: 1, mobile: false });
  if (reduced) await send('Emulation.setEmulatedMedia', { features: [{ name: 'prefers-reduced-motion', value: 'reduce' }] });
  await send('Page.navigate', { url: `${origin}/${FILE}` });
  for (let i = 0; i < 100; i++) { if (await evaluate('document.readyState === "complete"').catch(() => false)) break; await sleep(60); }
  await evaluate('try{localStorage.clear()}catch(e){}');
  await send('Page.navigate', { url: `${origin}/${FILE}` });
  for (let i = 0; i < 150; i++) {
    if (await evaluate('document.readyState === "complete" && (typeof window.drConsole !== "undefined" || !document.getElementById("app"))').catch(() => false)) break;
    await sleep(60);
  }
  const body = await readFile(CHECK, 'utf8');
  result = await evaluate(`(async () => {
    const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
    const c = window.drConsole;
    const $ = (s) => document.querySelector(s);
    const $$ = (s) => Array.from(document.querySelectorAll(s));
    const text = (s) => { const el = $(s); return el ? el.textContent.replace(/\\s+/g, ' ').trim() : null; };
    const chipText = (el) => { const k = el.querySelector('.k'); const kt = k ? k.textContent : ''; return (kt + ' ' + el.textContent.slice(kt.length)).replace(/\\s+/g, ' ').trim(); };
    const waitFor = async (fn, ms = 15000) => { const end = Date.now() + ms; while (Date.now() < end) { if (fn()) return true; await sleep(50); } return false; };
    ${body}
  })()`);
} catch (e) {
  result = { ok: false, error: String((e && e.message) || e) };
} finally {
  try { if (ws) ws.close(); } catch {}
  if (chrome) {
    if (process.platform === 'win32') spawnSync('taskkill', ['/PID', String(chrome.pid), '/T', '/F'], { stdio: 'ignore' });
    else { try { process.kill(chrome.pid); } catch {} }
  }
  server.close();
  for (let i = 0; i < 10 && profile; i++) {                 // the profile can stay locked for a moment after the kill
    try { await rm(profile, { recursive: true, force: true }); break; } catch { await sleep(200); }
  }
}
console.log(JSON.stringify(result, null, 1));
process.exit(!result || result.ok === false ? 1 : 0);
EOF
echo harness written
```

Smoke it once on today's tree: `printf 'return { ok: true, stage: c.stage() };\n' > "$TEMP/dr-probe/t0-smoke.js" && node "$TEMP/dr-probe/probe.mjs" docs/design/prototype index.html "$TEMP/dr-probe/t0-smoke.js"; ls "$TEMP/dr-probe/profiles"` → prints `{ "ok": true, "stage": "idle" }`, exits 0, and `profiles/` is empty (the run removed its own profile). If `ok: false` with `error`, fix the harness before going on.

- [ ] **Step 2: Write the failing check — the seven fixture outcomes and their chip notes (AC9)**

```bash
cat > "$TEMP/dr-probe/t1-chips.js" <<'EOF'
const want = {
  "8f2c1d90": ["running", "Running · pass 1 of 2"],
  "c3d7e5f1": ["running", "Running · pass 1 of 2"],
  "b41e77aa": ["report",  "Completed · review accepted · 0.86 · 1 target not found"],
  "7c0d13ff": ["report",  "Partially completed · extra passes used · 1 target not found"],
  "5ff1ab07": ["report",  "Partially completed · not accepted · 0.71"],
  "9ea4c220": ["report",  "Partially completed · review unavailable"],
  "2ad900b1": ["failed",  "Failed · halted"]
};
const got = {};
for (const id of Object.keys(want)) {
  c.open(id);
  await waitFor(() => c.stage() === want[id][0], 3000);
  got[id] = [c.stage(), text('#topbarStatus'), (c.sessions.find((s) => s.id === id) || {}).q || null];
}
const ok = Object.keys(want).every((id) => got[id][0] === want[id][0] && got[id][1] === want[id][1])
  && got["b41e77aa"][2] === "What is the levelised cost of grid-scale battery storage in 2025?";
return { ok, got };
EOF
node "$TEMP/dr-probe/probe.mjs" docs/design/prototype index.html "$TEMP/dr-probe/t1-chips.js"
```

Expected: exit 1, `"ok": false`; `got["b41e77aa"][1]` is `"Completed · quality gates accepted"`; `got["c3d7e5f1"]` still shows `8f2c1d90`'s state (`["running", "Running · iteration 1 of 3", null]`) because opening an unknown id changes nothing.

- [ ] **Step 3: Replace the fixtures (`:1450-1482`, from `/* ═══ the client's own session ledger ═══ */` through the closing `];`)**

```js
  /* ═══ the client's own session ledger ═══
     Shapes follow ResearchSessionResponse (api/models.py:114-164): `iteration` is the API's
     zero-based value, `passes` is 1 + max_extra_passes, `review` is semantic_review_status +
     semantic_review_score, `coverage` and `evidenceCounts` are the response blocks of the same
     names and are null while a run is live (or, for evidence_counts, when a halt left neither a
     composition nor a quality snapshot — runtime/outcome.py:525). Playback sessions carry the
     outcome they end on under `final`, applied when their script finishes. */
  var BATTERY_COUNTS = { read_records:31, network_reads:24, cache_reads:7, unique_works:14, publishers:9,
                         source_urls:17, findings:18, assessed_sources:14, cited_assessed_sources:12,
                         verified_findings:12, corrected_findings:2, quoted_findings:1, dropped_findings:3,
                         context_unchecked_findings:1, cited_findings:11 };
  /* what a run submitted from the composer ends on: one pass, accepted at 0.86 (spec §4.3) */
  var COMPOSER_FINAL = {
    review:{ status:"scored", score:0.86 },
    coverage:{ required_targets:4, answered_targets:4, missing_required_target_ids:[], not_found_target_ids:[] },
    evidenceCounts:BATTERY_COUNTS
  };
  var STATEMENT_CHECK_ERROR = { type:"report_writer_statement_check_failed", recoverable:true, source:"report_writer",
    message:"The statement check could not judge one batch of sentences; they were kept unchecked and marked in the evidence log." };
  var CONTEXT_CHECK_ERROR = { type:"evidence_verifier_context_check_failed", recoverable:true, source:"evidence_verifier",
    message:"The context check could not judge one batch of figures; they were kept and marked context unchecked." };
  var SESSIONS = [
    { id:"8f2c1d90", q:"What are the current constraints on grid-scale battery storage deployment?",
      status:"running", routeReason:null, iteration:0, passes:2, started:"2026-09-16T14:02:11Z", finished:null, durationSeconds:null,
      reportPath:null, evidencePath:null, qualityPath:null, trace:null, group:"Today", playback:true, script:"extra_pass",
      review:null, coverage:null, evidenceCounts:null,
      final:{ review:{ status:"scored", score:0.86 },
              coverage:{ required_targets:4, answered_targets:4, missing_required_target_ids:[], not_found_target_ids:[] },
              evidenceCounts:BATTERY_COUNTS },
      errors:[STATEMENT_CHECK_ERROR, CONTEXT_CHECK_ERROR] },
    { id:"c3d7e5f1", q:"How do lithium iron phosphate and NMC cells compare for grid-scale storage?",
      status:"running", routeReason:null, iteration:0, passes:2, started:"2026-09-16T13:21:40Z", finished:null, durationSeconds:null,
      reportPath:null, evidencePath:null, qualityPath:null, trace:null, group:"Today", playback:true, script:"redraft",
      review:null, coverage:null, evidenceCounts:null,
      final:{ review:{ status:"scored", score:0.84 },
              coverage:{ required_targets:3, answered_targets:3, missing_required_target_ids:[], not_found_target_ids:[] },
              evidenceCounts:BATTERY_COUNTS },
      errors:[] },
    { id:"b41e77aa", q:"What is the levelised cost of grid-scale battery storage in 2025?",
      status:"completed", routeReason:"report_accepted", iteration:1, passes:2, started:"2026-09-16T11:48:03Z", finished:"2026-09-16T11:57:15Z", durationSeconds:552,
      reportPath:"api-output/report.md", evidencePath:"api-output/report-evidence.md", qualityPath:"api-output/report-quality.json",
      trace:"https://smith.langchain.com/o/example/r/b41e77aa", group:"Today",
      review:{ status:"scored", score:0.86 },
      coverage:{ required_targets:4, answered_targets:3, missing_required_target_ids:["T04"], not_found_target_ids:["T04"] },
      evidenceCounts:BATTERY_COUNTS,
      errors:[STATEMENT_CHECK_ERROR] },
    { id:"7c0d13ff", q:"What limits solid-state electrolyte scale-up for EV cells?",
      status:"max_iterations", routeReason:"extra_passes_exhausted", iteration:1, passes:2, started:"2026-09-16T09:15:40Z", finished:"2026-09-16T09:29:42Z", durationSeconds:842,
      reportPath:"api-output/report.md", evidencePath:"api-output/report-evidence.md", qualityPath:"api-output/report-quality.json",
      trace:"https://smith.langchain.com/o/example/r/7c0d13ff", group:"Today",
      review:{ status:"scored", score:0.74 },
      coverage:{ required_targets:4, answered_targets:3, missing_required_target_ids:["T04"], not_found_target_ids:["T04"] },
      evidenceCounts:BATTERY_COUNTS,
      errors:[CONTEXT_CHECK_ERROR] },
    { id:"5ff1ab07", q:"Which fire-code standards govern BESS siting in the U.S.?",
      status:"incomplete", routeReason:"report_not_accepted", iteration:0, passes:1, started:"2026-09-15T13:40:02Z", finished:"2026-09-15T13:47:00Z", durationSeconds:418,
      reportPath:"api-output/report.md", evidencePath:"api-output/report-evidence.md", qualityPath:"api-output/report-quality.json",
      trace:null, group:"Yesterday",
      review:{ status:"scored", score:0.71 },
      coverage:{ required_targets:3, answered_targets:3, missing_required_target_ids:[], not_found_target_ids:[] },
      evidenceCounts:BATTERY_COUNTS,
      errors:[] },
    { id:"9ea4c220", q:"What is the measured cost of interconnection queue delay?",
      status:"incomplete", routeReason:"review_unavailable", iteration:0, passes:2, started:"2026-09-15T15:04:10Z", finished:"2026-09-15T15:15:40Z", durationSeconds:690,
      reportPath:"api-output/report.md", evidencePath:"api-output/report-evidence.md", qualityPath:"api-output/report-quality.json",
      trace:"https://smith.langchain.com/o/example/r/9ea4c220", group:"Yesterday",
      review:{ status:"provider_failed", score:null },
      coverage:{ required_targets:2, answered_targets:2, missing_required_target_ids:[], not_found_target_ids:[] },
      /* 4 of 5 = 0.80: paints yellow on purpose (spec §4.4, DESIGN.md §3.6) */
      evidenceCounts:Object.assign({}, BATTERY_COUNTS, { assessed_sources:5, cited_assessed_sources:4 }),
      errors:[] },
    { id:"2ad900b1", q:"Compare EU and UK grid connection reform outcomes.",
      status:"failed", routeReason:"halted", iteration:0, passes:2, started:"2026-09-15T17:22:27Z", finished:"2026-09-15T17:22:31Z", durationSeconds:4,
      reportPath:null, evidencePath:null, qualityPath:null, trace:"https://smith.langchain.com/o/example/r/2ad900b1", group:"Yesterday",
      review:null, coverage:null, evidenceCounts:null,
      errors:[{type:"graph_provider_configuration_error", recoverable:false, source:"graph",
               message:"The model provider is not configured, so the research run stopped.",
               details:{exception_type:"ValidationError"}}] }
  ];
```

- [ ] **Step 4: Replace `STATUS`, the comment above `passNumber`, `statusNote` and its comment (`:1484-1519`, from `var STATUS = {` through the closing `}` of `statusNote`)**

Keep `chipHTML()` (`:1520-1526`) as it is. Replace the range with:

```js
  /* Label and dot per API status (api/models.py:13-19). The second clause is not a constant:
     it is built from the session's own review, coverage and pass fields — see statusNote(). */
  var STATUS = {
    running:        { label:"Running",             dot:"dot-live"   },
    completed:      { label:"Completed",           dot:"dot-ok"     },
    max_iterations: { label:"Partially completed", dot:"dot-warn"   },
    incomplete:     { label:"Partially completed", dot:"dot-warn"   },
    failed:         { label:"Failed",              dot:"dot-danger" }
  };
  /* `session.iteration` is the API value and is zero-based: the first pass carries
     `iteration: 0`. The interface counts passes the way an operator does, from 1, so the
     display adds one here rather than changing the stored value — the number on screen is a
     pass number, the number in the session is an index. This is the only place the offset lives. */
  function passNumber(iteration){
    var n = Number(iteration);
    return (isFinite(n) ? n : 0) + 1;
  }
  /* The ceiling: 1 + max_extra_passes, carried by the session as `passes`. A session that has
     none drops the "of P" clause rather than inventing a ceiling the server never confirmed. */
  function passTotal(s){
    var n = Number(s && s.passes);
    return (isFinite(n) && n > 0) ? n : null;
  }
  function passText(s){
    var total = passTotal(s);
    return "pass " + passNumber(s.iteration) + (total === null ? "" : " of " + total);
  }
  /* Scores print with two decimals; null is "no score", never 0 (models.py: semantic_review_score). */
  function fmtScore(v){
    return (typeof v === "number" && isFinite(v)) ? v.toFixed(2) : null;
  }
  /* coverage.not_found_target_ids → " · 1 target not found" / " · n targets not found" / "" (spec §4.2) */
  function notFoundClause(s){
    var ids = (s && s.coverage && s.coverage.not_found_target_ids) || [];
    if(!ids.length) return "";
    return " · " + ids.length + (ids.length === 1 ? " target not found" : " targets not found");
  }
  /* The chip's second clause, one rule per API status (spec §4.2):
       running         pass p of P                       (from the stream while live)
       completed       review accepted · {score}         + not-found clause   (completed ⇔ report_accepted)
       max_iterations  extra passes used                 + not-found clause
       incomplete      not accepted · {score}            when the review was scored (R3: a gate can
                                                         block acceptance while the review passed)
                       review unavailable                otherwise (incomplete / provider_failed / null)
       failed          halted */
  function statusNote(s){
    var score = fmtScore(s.review && s.review.score);
    switch(s.status){
      case "completed":      return "review accepted" + (score === null ? "" : " · " + score) + notFoundClause(s);
      case "max_iterations": return "extra passes used" + notFoundClause(s);
      case "incomplete":     return (s.review && s.review.status === "scored" && score !== null) ? "not accepted · " + score : "review unavailable";
      case "failed":         return "halted";
      default:               return passText(s);
    }
  }
```

- [ ] **Step 5: Update the comment above `sessionSettings` (`:1553-1558`)** — the body is unchanged:

```js
  /* Settings actually used by a session: { model, thinking, outputDir, extraPasses }. A session
     keeps its own copy because the API echoes neither the query nor the overrides back (api-gaps 1.3). */
  function sessionSettings(session){
    return (session && session.settings) || settings;
  }
```

- [ ] **Step 6: Run the chip check again**

Run: `node "$TEMP/dr-probe/probe.mjs" docs/design/prototype index.html "$TEMP/dr-probe/t1-chips.js"`
Expected: exit 0, `"ok": true`, every `got[id][1]` equal to `want[id][1]`. (The two playback sessions play the old script until Task 3 — the chip text is what this task owns.)

- [ ] **Step 7: Scoped stale-name check for this region**

Run: `rg -n -e 'quality gates accepted' -e 'budget exhausted' -e 'no critique recorded' -e 'iteration \{i\} of' -e 'synthesizer_invalid_draft' docs/design/prototype/index.html`
Expected before steps 3–4: six lines (`:1467`, `:1485`, `:1486`, `:1487`, `:1488`, `:1502`). Expected now: prints nothing. (The bare word `synthesizer` still appears in `STAGES`, `buildEvents`, `weigh`, `currentStageIndex` and `BLURB` — Tasks 3–4 own those lines; do not touch them here.)

- [ ] **Step 8: Review Focus 4 — empty and plural not-found lists**

```bash
cat > "$TEMP/dr-probe/t1-clause.js" <<'EOF'
const s = c.sessions.find((x) => x.id === "7c0d13ff");
s.coverage.not_found_target_ids = [];
c.open("7c0d13ff"); await waitFor(() => c.stage() === "report", 3000);
const empty = text('#topbarStatus');
s.coverage.not_found_target_ids = ["T03", "T04"];
c.open("b41e77aa"); await waitFor(() => c.stage() === "report", 3000);
c.open("7c0d13ff"); await waitFor(() => c.stage() === "report", 3000);
const two = text('#topbarStatus');
return { ok: empty === "Partially completed · extra passes used" && two === "Partially completed · extra passes used · 2 targets not found", empty, two };
EOF
node "$TEMP/dr-probe/probe.mjs" docs/design/prototype index.html "$TEMP/dr-probe/t1-clause.js"
```

Expected: exit 0, `"ok": true`.

- [ ] **Step 9: Commit**

```bash
git add docs/design/prototype/index.html
git commit -m "design(prototype): fixtures and chip notes for the Evidence Verifier statuses"
```

---

### Task 2: Composer, settings strip and request body (spec §4.3)

**Files:**
- Modify: `docs/design/prototype/index.html:1109-1144` (popover rows from `<div class="pop-row">` / `<span class="lbl" id="lblModel">Model</span>` through the Refinements stepper), `:1145-1146` (`#pillModel`, `#pillEffort`), `:718-720` (the `/* Refinement stepper.` CSS comment), `:1423-1430` (`var MODELS`, `var settings`), `:1559-1569` (`optionsHTML`), `:1953` (`syncEffort();` inside `togglePop`), `:1955-2017` (`syncEffort` through `buildOverrides`), `:2111-2126` (`wireComposer` handlers for `#segEffort`, `#refineMinus`, `#refinePlus`), `:2169-2178` (`submitResearch` session literal), `:2960-2975` (`restoreSettings`)
- Test: `$TEMP/dr-probe/t2-composer.js`, `$TEMP/dr-probe/t2-zero.js`

**Interfaces:**
- Consumes: `COMPOSER_FINAL`, `sessionSettings()` (Task 1).
- Produces: `settings = { model, thinking, outputDir, extraPasses }`; `EXTRA_MIN/EXTRA_MAX/EXTRA_DEFAULT`; `clampExtra(n)`, `syncExtraUI()`, `stepExtra(delta)`, `syncEffortLine()`, `syncPills()`, `buildRequest(q) → the exact POST body`; the submitted session carries `passes = 1 + extraPasses`, `settings.extraPasses`, `request`, `script:null`, `final: COMPOSER_FINAL`; `optionsHTML(session)` renders the five strip chips. Task 4 reads `session.script` and `session.final`.

- [ ] **Step 1: Write the failing composer check (AC7, AC8 on all three stages)**

```bash
cat > "$TEMP/dr-probe/t2-composer.js" <<'EOF'
$('#plusBtn').click(); await sleep(50);
const models = $$('#segModel button').map((b) => [b.dataset.model, b.getAttribute('aria-pressed')]);
const effortSeg = !!$('#segEffort');
const line0 = text('#effortLine');
$$('#segThinking button').find((b) => b.dataset.thinking === 'disabled').click(); await sleep(30);
const line1 = text('#effortLine');
$$('#segThinking button').find((b) => b.dataset.thinking === 'enabled').click(); await sleep(30);
const value0 = text('#extraValue'), minus0 = $('#extraMinus').disabled, plus0 = $('#extraPlus').disabled;
$('#extraMinus').click(); const value1 = text('#extraValue'), minus1 = $('#extraMinus').disabled;
$('#extraPlus').click(); $('#extraPlus').click(); const value2 = text('#extraValue'), plus2 = $('#extraPlus').disabled;
$('#extraMinus').click();  // back to 1
const notes = $$('#settingsPop .pop-note, #settingsPop p').map((p) => p.textContent.trim()).filter(Boolean);
$('#popClose').click();
const wantStrip = ['model deepseek-flash', 'thinking enabled', 'effort per agent', 'extra passes 1', 'out output/'];
const strip = (id) => $$('#' + id + ' .opt').map(chipText);
c.submit("What is the current state of grid-scale battery storage?");
await waitFor(() => c.stage() === 'submitted', 3000);
const submitted = strip('submittedOpts');
await waitFor(() => c.stage() === 'running', 6000);
const running = strip('runningOpts');
c.finish();
await waitFor(() => c.stage() === 'report', 3000);
const report = strip('reportOpts');
const request = c.sessions[0].request;
const wantBody = {"query": "What is the current state of grid-scale battery storage?", "max_iterations": 1, "output_format": "markdown",
  "config_overrides": {"llm": {"model": "deepseek-flash", "thinking_mode": "enabled"}, "output": {"directory": "output/"}}};
const same = (a, b) => JSON.stringify(a) === JSON.stringify(b);
const ok = same(models, [["deepseek-flash","true"],["deepseek-v4-flash","false"],["deepseek-v4-pro","false"]])
  && !effortSeg && line0 === 'effort per agent: planner max · reviewer max · others high' && line1 === 'effort: not sent (thinking disabled)'
  && value0 === '1' && !minus0 && !plus0 && value1 === '0' && minus1 && value2 === '2' && plus2
  && notes.length === 1
  && same(submitted, wantStrip) && same(running, wantStrip) && same(report, wantStrip)
  && same(request, wantBody) && c.sessions[0].passes === 2;
return { ok, models, effortSeg, line0, line1, stepper:[value0, minus0, plus0, value1, minus1, value2, plus2], notes, submitted, running, report, request };
EOF
node "$TEMP/dr-probe/probe.mjs" docs/design/prototype index.html "$TEMP/dr-probe/t2-composer.js"
```

Expected: exit 1 — `error` names `#extraValue`/`#extraMinus` as null (`Cannot read properties of null`), or `ok: false` with two model buttons and a strip of `["model v4-flash", "thinking enabled", "effort high", "out output/"]`.

- [ ] **Step 2: Replace the popover rows (`:1109-1144`)** — from the `<div class="pop-row">` holding `id="lblModel"` through the closing `</div>` of the Refinements row:

```html
                    <div class="pop-row">
                      <span class="lbl" id="lblModel">Model</span>
                      <div class="seg" role="group" aria-labelledby="lblModel" id="segModel">
                        <button type="button" data-model="deepseek-flash" aria-pressed="true">flash</button>
                        <button type="button" data-model="deepseek-v4-flash" aria-pressed="false">v4-flash</button>
                        <button type="button" data-model="deepseek-v4-pro" aria-pressed="false">v4-pro</button>
                      </div>
                    </div>
                    <div class="pop-row">
                      <span class="lbl" id="lblThinking">Thinking</span>
                      <div class="seg" role="group" aria-labelledby="lblThinking" id="segThinking">
                        <button type="button" data-thinking="enabled" aria-pressed="true">enabled</button>
                        <button type="button" data-thinking="disabled" aria-pressed="false">disabled</button>
                      </div>
                      <!-- Effort is not a control. Every role has its own configured effort
                           (config.yaml:32-63) and a global override would be shadowed by them; the
                           line states what is in force and nothing else (DESIGN.md §3.2). -->
                      <p class="pop-note" id="effortLine" aria-live="polite">effort per agent: planner max · reviewer max · others high</p>
                    </div>
                    <div class="pop-row">
                      <span class="lbl" for="outputDir">Output directory</span>
                      <input class="input mono-in" id="outputDir" value="output/">
                    </div>
                    <div class="pop-row">
                      <span class="lbl" id="lblExtra">Extra passes</span>
                      <div class="stepper" role="group" aria-labelledby="lblExtra" id="stepExtra">
                        <button type="button" id="extraMinus" aria-label="Fewer extra passes">
                          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="square" aria-hidden="true"><path d="M5 12h14"></path></svg>
                        </button>
                        <span class="stepper-v mono" id="extraValue" role="status" aria-live="polite">1</span>
                        <button type="button" id="extraPlus" aria-label="More extra passes">
                          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="square" aria-hidden="true"><path d="M12 5v14M5 12h14"></path></svg>
                        </button>
                      </div>
                    </div>
```

Then replace the two pills (`:1145-1146`) with three:

```html
                <span class="setting-pill" id="pillModel"><span class="v" id="pillModelV">flash</span></span>
                <span class="setting-pill" id="pillThinking"><span class="k">thinking</span><span class="v" id="pillThinkingV">enabled</span></span>
                <span class="setting-pill" id="pillExtra"><span class="k">extra passes</span><span class="v" id="pillExtraV">1</span></span>
```

- [ ] **Step 3: Replace the registry and settings (`:1423-1430`, `/* ═══ model capability registry` through `var settings = …;`)**

```js
  /* ═══ model capability registry, mirrored from providers/capabilities.py:71-77 ═══
     deepseek: ^deepseek-(flash|v4-flash|v4-pro)$ — thinking enabled|disabled; efforts are
     configured per role (config.yaml:32-63) and are not a request-time control here. */
  var MODELS = {
    "deepseek-flash":    { label:"flash"    },
    "deepseek-v4-flash": { label:"v4-flash" },
    "deepseek-v4-pro":   { label:"v4-pro"   }
  };
  var settings = { model:"deepseek-flash", thinking:"enabled", outputDir:"output/", extraPasses:1 };
```

- [ ] **Step 4: Replace `optionsHTML` (`:1559-1569`, `function optionsHTML(session){`)**

```js
  /* The settings strip: five mono chips, in this order, exactly as submitted (there is no
     server echo — api-gaps 1.3). `out` is shown only when a directory was sent. */
  function optionsHTML(session){
    var s = sessionSettings(session);
    var out = '<span class="opt"><span class="k">model</span>' + s.model + "</span>"
            + '<span class="opt"><span class="k">thinking</span>' + s.thinking + "</span>"
            + (s.thinking === "disabled"
                ? '<span class="opt"><span class="k">effort</span>not sent</span>'
                : '<span class="opt"><span class="k">effort</span>per agent</span>')
            + '<span class="opt"><span class="k">extra passes</span>' + s.extraPasses + "</span>";
    if(s.outputDir) out += '<span class="opt"><span class="k">out</span>' + s.outputDir + "</span>";
    return out;
  }
```

- [ ] **Step 5: Replace the effort/pill/stepper/override functions (`:1955-2017`, from `function syncEffort(){` through the end of `buildOverrides`)**

```js
  /* The one sentence in the panel. Effort is fixed per role (planner max, report_reviewer max,
     the other four high — config.yaml:32-63), so there is nothing to choose; with thinking off
     the resolver sends no effort at all (capabilities.py:77). */
  function syncEffortLine(){
    var line = document.getElementById("effortLine");
    if(!line) return;
    var off = settings.thinking === "disabled";
    line.classList.toggle("warn", off);
    line.textContent = off ? "effort: not sent (thinking disabled)" : "effort per agent: planner max · reviewer max · others high";
  }
  function syncPills(){
    var cap = MODELS[settings.model];
    setText("pillModelV", cap ? cap.label : settings.model);
    setText("pillThinkingV", settings.thinking);
    setText("pillExtraV", String(settings.extraPasses));
  }
  /* Extra-pass budget bounds. The request field is `max_iterations`, validated `ge=0`
     (api/models.py:48) and passed to the graph as max_extra_passes (api/app.py:183); the
     server default is 1 (config.yaml:211). Zero is a real budget: no extra pass, ever. The
     ceiling is this console's: each extra pass can add a large share of a 70–110 minute run. */
  var EXTRA_MIN = 0;
  var EXTRA_MAX = 2;
  var EXTRA_DEFAULT = 1;   /* matches graph.max_extra_passes in config.yaml */

  function clampExtra(n){
    n = Math.round(Number(n));
    if(!isFinite(n)) n = EXTRA_DEFAULT;
    return Math.max(EXTRA_MIN, Math.min(EXTRA_MAX, n));
  }
  /* The stepper is a bounded control: the buttons disable at the ends so the limit
     is visible rather than silently enforced by a clamp. */
  function syncExtraUI(){
    var v = document.getElementById("extraValue");
    var minus = document.getElementById("extraMinus");
    var plus = document.getElementById("extraPlus");
    settings.extraPasses = clampExtra(settings.extraPasses);
    if(v) v.textContent = String(settings.extraPasses);
    if(minus) minus.disabled = settings.extraPasses <= EXTRA_MIN;
    if(plus) plus.disabled = settings.extraPasses >= EXTRA_MAX;
  }
  function stepExtra(delta){
    settings.extraPasses = clampExtra(settings.extraPasses + delta);
    syncExtraUI();
  }

  function syncSettingsUI(){
    syncSeg("segModel", "data-model", settings.model);
    syncSeg("segThinking", "data-thinking", settings.thinking);
    var dir = document.getElementById("outputDir");
    if(dir && settings.outputDir) dir.value = settings.outputDir;
    syncEffortLine();
    syncPills();
    syncExtraUI();
  }
  /* The request body, exactly as POST /research would receive it (spec §4.3). No
     reasoning_effort: a global value is shadowed by every role's own (utils/config.py:85-107).
     `max_iterations` is always present — 0 is a budget, not an absence. */
  function buildRequest(q){
    var dirEl = document.getElementById("outputDir");
    var dir = dirEl && dirEl.value ? dirEl.value.trim() : "";
    var overrides = { llm: { model: settings.model, thinking_mode: settings.thinking } };
    if(dir) overrides.output = { directory: dir };
    return { query: q, max_iterations: clampExtra(settings.extraPasses), output_format: "markdown", config_overrides: overrides };
  }
```

In `togglePop()` (`:1953`) replace the call `syncEffort();` with `syncEffortLine();`. In the stylesheet, change the first words of the stepper comment at `:718` from `/* Refinement stepper. A bounded numeric control` to `/* Extra-passes stepper. A bounded numeric control` (the rest of the three-line comment stays).

- [ ] **Step 6: Rewire the handlers and the submitted session**

In `wireComposer()` (`:2111-2126`) delete the `#segEffort` listener block and replace the two refine listeners with:

```js
    form.querySelector("#extraMinus").addEventListener("click", function(){ stepExtra(-1); });
    form.querySelector("#extraPlus").addEventListener("click", function(){ stepExtra(1); });
```

In `submitResearch()` (`:2169-2178`) replace from `var budget = clampRefinements(settings.refinements);` through `overrides: buildOverrides()` with:

```js
    var budget = clampExtra(settings.extraPasses);
    var request = buildRequest(q);
    var session = {
      id: Math.random().toString(16).slice(2, 10), q: q, status: "running", routeReason: null, iteration: 0,
      /* P = 1 + the extra-pass budget; the scripted run is one accepted pass whatever the budget (§4.3) */
      passes: 1 + budget,
      started: new Date().toISOString(), finished: null, durationSeconds: null,
      reportPath: null, evidencePath: null, qualityPath: null, trace: null,
      review: null, coverage: null, evidenceCounts: null,
      errors: [], group: "Today", playback: true, script: null, final: COMPOSER_FINAL,
      settings: { model: settings.model, thinking: settings.thinking, outputDir: settings.outputDir, extraPasses: budget },
      request: request
    };
```

In `restoreSettings()` (`:2960-2975`) replace the body with:

```js
    if(session.settings){
      settings.model = session.settings.model;
      settings.thinking = session.settings.thinking;
      settings.outputDir = session.settings.outputDir;
      /* the budget this session ran with, so reopening it shows its own number */
      if(typeof session.settings.extraPasses === "number") settings.extraPasses = clampExtra(session.settings.extraPasses);
      else if(typeof session.passes === "number") settings.extraPasses = clampExtra(session.passes - 1);
    }
    /* the composer only exists while idle, so only sync it there */
    if(state.stage === "idle") syncSettingsUI();
```

Grep for leftovers: `rg -n 'clampRefinements|\.refinements|refinements:|refineMinus|refinePlus|segEffort|buildOverrides|syncEffort\(|settings\.effort|REFINE_' docs/design/prototype/index.html` must print nothing. (The bare word `refinements` is not in this pattern on purpose: it still appears in the `/* ═══ refinements ═══` CSS comment at today's `:583-589`, which Task 4 step 3 rewrites.)

- [ ] **Step 7: Run the composer check; then Review Focus 2 (`max_iterations: 0` present)**

Run: `node "$TEMP/dr-probe/probe.mjs" docs/design/prototype index.html "$TEMP/dr-probe/t2-composer.js"` → exit 0, `"ok": true`, `submitted`, `running` and `report` each equal to the five chips.

```bash
cat > "$TEMP/dr-probe/t2-zero.js" <<'EOF'
$('#plusBtn').click(); await sleep(50); $('#extraMinus').click(); $('#popClose').click();
c.submit("Does congestion pricing reduce particulate pollution?");
await waitFor(() => c.stage() === 'running', 6000);
const r = c.sessions[0].request;
return { ok: Object.prototype.hasOwnProperty.call(r, 'max_iterations') && r.max_iterations === 0 && c.sessions[0].passes === 1
             && text('#topbarStatus') === 'Running · pass 1 of 1', request: r, chip: text('#topbarStatus') };
EOF
node "$TEMP/dr-probe/probe.mjs" docs/design/prototype index.html "$TEMP/dr-probe/t2-zero.js"
```

Expected: exit 0, `"ok": true`, `"max_iterations": 0` present in `request`.

- [ ] **Step 8: Commit**

```bash
git add docs/design/prototype/index.html
git commit -m "design(prototype): composer sends model, thinking and an extra-pass budget; effort is a read-only line"
```

---

### Task 3: Stage rows, blurbs and the scripted stream (spec §3.3, §4.1 spine table, §4.6 `buildEvents`, AC2, AC3)

**Files:**
- Modify: `docs/design/prototype/index.html:2199-2221` (`/* ═══ pipeline ═══ */` comment + `var STAGES`), `:2450-2530` (`/* ═══ scripted stream` + `function buildEvents`), `:2538-2547` (`function weigh`), `:2549-2561` (the `/* Scripts are built per session` comment, `var SCRIPTS`, `scriptFor`), `:2659-2676` (`currentStageIndex` order list, `var BLURB`)
- Test (throwaway): `$TEMP/dr-probe/replay_keys.py`, `$TEMP/dr-probe/check_keys.mjs`, `$TEMP/dr-probe/t3-stages.js`

**Interfaces:**
- Consumes: `session.script` ∈ `"extra_pass" | "redraft" | null`, `session.passes` (Tasks 1–2).
- Produces: `STAGES[7]` (`{id,label,meta}`), `AGENT_ORDER[7]`, `BLURB{}`, `buildEvents({ loop, maxExtraPasses, sessionId }) → ev[]` (pure: no DOM, no closure over page state; `ev = {type, source, message, metadata}`), `HALTED_EVENTS[]` (pure literal), `weigh(events)`, `scriptFor(session)`. Task 4 consumes all of them; the throwaway key check parses `buildEvents` and `HALTED_EVENTS` out of the file by name, so keep those two names and keep their bodies free of `{`, `}`, `[`, `]` characters inside string literals.

- [ ] **Step 1: Write the offline replay capture (spec §4.6 step 1, part 1) and run it once**

```bash
cat > "$TEMP/dr-probe/replay_keys.py" <<'EOF'
"""Throwaway: replay two scripted cases offline and record {event_type: [metadata keys]}."""
import json, shutil, sys, tempfile
from pathlib import Path
from deep_research.e2e_evaluation.replay import run_replay_scenario, network_denied, offline_credentials
from deep_research.e2e_evaluation.replay_matrix import scenario_by_id
from deep_research.graph.events import node_skipped_event

CASES = ["missing-target-triggers-one-extra-pass", "scoped-redraft-after-a-named-defect"]
captured: dict[str, set[str]] = {}
counts: dict[str, int] = {}
root = Path(tempfile.mkdtemp(prefix="dr-replay-"))
try:
    for case in CASES:
        with network_denied(), offline_credentials():
            run = run_replay_scenario(scenario_by_id(case), root=root / case)
        counts[case] = len(run.state.events)
        for e in run.state.events:
            captured.setdefault(e.event_type, set()).update(e.metadata.keys())
finally:
    shutil.rmtree(root, ignore_errors=True)
out = Path(sys.argv[1])
out.write_text(json.dumps({
    "captured": {k: sorted(v) for k, v in sorted(captured.items())},
    "skipped_keys": sorted(node_skipped_event("researcher", iteration=0).metadata.keys()),
    "event_counts": counts,
}, indent=1), encoding="utf-8")
print(f"captured {len(captured)} event types from {counts} -> {out}")
EOF
PYTHONPATH=src PYTHONDONTWRITEBYTECODE=1 "C:/Users/Rahul Krishnamoorthy/OneDrive/Documents/Python Scripts/deep-research/.venv/Scripts/python.exe" "$TEMP/dr-probe/replay_keys.py" "$TEMP/dr-probe/captured.json"
```

Expected (observed 2026-09-26, ~2 s): `captured 21 event types from {'missing-target-triggers-one-extra-pass': 72, 'scoped-redraft-after-a-named-defect': 49} -> …captured.json`. No network, no provider call; the temp root is deleted by the script. (PowerShell alternative: `$env:PYTHONPATH='src'; $env:PYTHONDONTWRITEBYTECODE='1'; & 'C:/Users/Rahul Krishnamoorthy/OneDrive/Documents/Python Scripts/deep-research/.venv/Scripts/python.exe' "$env:TEMP/dr-probe/replay_keys.py" "$env:TEMP/dr-probe/captured.json"`.)

- [ ] **Step 2: Write the key check (spec §4.6 step 1, part 2)**

```bash
cat > "$TEMP/dr-probe/check_keys.mjs" <<'EOF'
// Throwaway: every event type and metadata key the prototype emits or reads must exist in the replay capture.
// usage: node check_keys.mjs <index.html> <captured.json> [--emitted-only]
import { readFileSync } from 'node:fs';
const [htmlPath, jsonPath] = process.argv.slice(2, 4);
const emittedOnly = process.argv.includes('--emitted-only');
const html = readFileSync(htmlPath, 'utf8').replace(/\r\n/g, '\n');
const { captured, skipped_keys } = JSON.parse(readFileSync(jsonPath, 'utf8'));
captured['graph.node.skipped'] = skipped_keys;   // (c): the matrix has no halted case
const failures = [];
function matched(src, startRe, open, close) {
  const m = startRe.exec(src); if (!m) throw new Error('missing ' + startRe);
  let i = src.indexOf(open, m.index), depth = 0;
  for (let j = i; j < src.length; j++) {
    if (src[j] === open) depth++; else if (src[j] === close) { depth--; if (depth === 0) return { text: src.slice(m.index, j + 1), start: m.index, end: j + 1 }; }
  }
  throw new Error('unbalanced ' + startRe);
}
let emitted = [];
try {
  const buildSrc = matched(html, /function buildEvents\(/, '{', '}').text;
  const buildEvents = new Function(buildSrc + '\nreturn buildEvents;')();
  const haltedSrc = matched(html, /var HALTED_EVENTS = \[/, '[', ']').text.replace(/^var HALTED_EVENTS = /, '');
  const HALTED_EVENTS = new Function('return ' + haltedSrc + ';')();
  emitted = [
    ...buildEvents({ loop: 'extra_pass', maxExtraPasses: 1, sessionId: 'x' }),
    ...buildEvents({ loop: 'redraft', maxExtraPasses: 1, sessionId: 'x' }),
    ...buildEvents({ loop: null, maxExtraPasses: 0, sessionId: 'x' }),   // the composer-submitted script
    ...HALTED_EVENTS,
  ];
} catch (e) { failures.push('cannot extract scripts: ' + e.message); }
const byType = {};
for (const ev of emitted) { byType[ev.type] ??= new Set(); Object.keys(ev.metadata || {}).forEach((k) => byType[ev.type].add(k)); }
for (const [type, keys] of Object.entries(byType)) {                       // (a) + (b) emitted
  if (!captured[type]) { failures.push(`emitted type not in capture: ${type}`); continue; }
  for (const k of keys) if (!captured[type].includes(k)) failures.push(`emitted key not in capture: ${type}.${k}`);
}
if (!emittedOnly) {                                                        // (b) read by handlers
  try {
    const table = matched(html, /var EVENT_HANDLERS = \{/, '{', '}');
    const re = /"([a-z_.]+)":\s*function\s*\(([^)]*)\)\s*\{/g; let m;
    while ((m = re.exec(table.text))) {
      const body = matched(table.text.slice(m.index), /function/, '{', '}').text;
      const reads = [...body.matchAll(/\bmd\.([A-Za-z_]\w*)/g)].map((x) => x[1]);
      if (!captured[m[1]]) { failures.push(`handler for a type not in capture: ${m[1]}`); continue; }
      for (const k of reads) if (!captured[m[1]].includes(k)) failures.push(`handler reads key not in capture: ${m[1]}.${k}`);
      if (m[1] === 'researcher.tool_call' && reads.length) failures.push(`researcher.tool_call handler must read no keys (spec §3.2 rule 1), reads ${reads}`);
    }
  } catch (e) { failures.push('cannot extract EVENT_HANDLERS: ' + e.message); }
}
console.log(`checked ${Object.keys(byType).length} emitted event types, ${Object.values(byType).reduce((n, s) => n + s.size, 0)} emitted keys${emittedOnly ? ' (emitted only)' : ' + handler reads'}`);
for (const f of failures) console.log('FAIL ' + f);
console.log(failures.length ? `${failures.length} failure(s)` : 'OK');
process.exit(failures.length ? 1 : 0);
EOF
node "$TEMP/dr-probe/check_keys.mjs" docs/design/prototype/index.html "$TEMP/dr-probe/captured.json" --emitted-only
```

Expected before the edit: exit 1 with `FAIL cannot extract scripts: missing /var HALTED_EVENTS = \[/` (the old `buildEvents(totalPasses)` and the missing halted list).

- [ ] **Step 3: Write the failing AC2 check**

```bash
cat > "$TEMP/dr-probe/t3-stages.js" <<'EOF'
c.open("8f2c1d90"); await waitFor(() => c.stage() === "running", 3000);
const rows = $$('#spine li').map((li) => [li.dataset.stage, li.querySelector('.stage-name').childNodes[0].textContent.trim(), li.querySelectorAll('.stage-meta')[0].textContent.trim()]);
const want = [["planner","Planning","1–10 sub-topics"],["researcher","Researching","search · scrape · read · memory"],
  ["source_evaluator","Evaluating sources","authority · recency · relevance"],["evidence_verifier","Verifying evidence","snippet on page · context check"],
  ["report_writer","Writing report","verified findings only · statement check"],["report_reviewer","Reviewing","7 dimensions · accept at mean 0.80"],
  ["finalize_report","Publishing","report · evidence log · quality record"]];
return { ok: JSON.stringify(rows) === JSON.stringify(want), rows };
EOF
node "$TEMP/dr-probe/probe.mjs" docs/design/prototype index.html "$TEMP/dr-probe/t3-stages.js"
```

Expected: exit 1 (`fact_checker`, `synthesizer`, `critic` rows).

- [ ] **Step 4: Replace the pipeline comment and `STAGES` (`:2199-2221`, from `/* ═══ pipeline ═══ */` through the `];` closing `STAGES`)**

```js
  /* ═══ pipeline ═══
     Seven rows, one per agent node of the Evidence Verifier graph (graph/state.py:38-46,
     orchestrator.py:184-211): planner → researcher → source_evaluator → evidence_verifier →
     report_writer → report_reviewer → finalize_report. `extra_pass` and `writer_redraft` are
     hops the graph runs between the reviewer and its destination; they never map to a row.
     Every caption is the engine's own behaviour, checked against the source rather than
     invented: the planner's 1–10 sub-topics (planner.py:82-83), the researcher's four tools
     (researcher.py:3023-3028), the evaluator's three scores (types.py:625-631), the verifier's
     snippet-on-page and context checks (evidence_verifier.py), the writer's statement check
     (report_writer.py:2005), the reviewer's seven dimensions and 0.80 acceptance mean
     (types.py:1118-1131), and the three published documents (report.py). */
  var STAGES = [
    { id:"planner",           label:"Planning",           meta:"1–10 sub-topics" },
    { id:"researcher",        label:"Researching",        meta:"search · scrape · read · memory" },
    { id:"source_evaluator",  label:"Evaluating sources", meta:"authority · recency · relevance" },
    { id:"evidence_verifier", label:"Verifying evidence", meta:"snippet on page · context check" },
    { id:"report_writer",     label:"Writing report",     meta:"verified findings only · statement check" },
    { id:"report_reviewer",   label:"Reviewing",          meta:"7 dimensions · accept at mean 0.80" },
    { id:"finalize_report",   label:"Publishing",         meta:"report · evidence log · quality record" }
  ];
  var AGENT_ORDER = STAGES.map(function(s){ return s.id; });
```

- [ ] **Step 5: Replace the scripted stream (`:2450-2530`, from `/* ═══ scripted stream — shape taken from the documented event catalogue ═══ */` through the `}` closing `buildEvents`)**

```js
  /* ═══ scripted stream ═══
     Event names and metadata keys are the engine's (graph/events.py; agents/*.py — spec §3.3),
     in the order the graph records them per pass: planner → researcher (per topic: started,
     tool calls, completed; then research.completed) → source_evaluator → evidence_verifier →
     report_writer (report.written, node.completed, then graph.quality.assessed from the node
     wrapper, nodes.py:300-313) → report_reviewer (report.reviewed, route.decided, node.completed,
     nodes.py:819-841) → the hop the route chose, or finalize_report and session.completed.
     Pure: no DOM, no page state — the offline key check evaluates this function by itself. */
  function buildEvents(opts){
    opts = opts || {};
    var budget = Math.max(0, Math.round(Number(opts.maxExtraPasses) || 0));
    var loop = opts.loop || null;     /* null (accepted first time) | "extra_pass" | "redraft" */
    var sessionId = opts.sessionId || "session";
    var ev = [];
    function push(type, source, message, metadata){
      ev.push({ type:type, source:source, message:message, metadata:metadata || {} });
    }
    function node(name, iteration, body){
      push("graph.node.started", "graph." + name, "Node " + name + " started.", { node:name, iteration:iteration });
      var before = ev.length;
      body();
      push("graph.node.completed", "graph." + name, "Node " + name + " completed.",
        { node:name, iteration:iteration, event_count: ev.length - before, error_count:0 });
    }
    var TOOLS = ["web_search", "web_scraper", "document_reader", "query_memory"];
    var targets = ["T01", "T02", "T03", "T04"];
    /* the two required targets pass 0 leaves without a verified finding — only in the extra-pass run;
       the redraft and one-pass runs answer every target, as their routes and final coverage say */
    var missing = loop === "extra_pass" ? ["T02", "T04"] : [];
    var proposal = 0;

    function researcher(iteration, topics, extra){
      node("researcher", iteration, function(){
        for(var t = 0; t < topics.length; t++){
          push("researcher.sub_topic.started", "researcher", "Sub-topic started.",
            { sub_topic:topics[t], priority: t < 2 ? 1 : 2, index:t, existing_sources: extra ? 14 : t * 2 });
          var calls = extra ? 2 : 3;
          for(var k = 0; k < calls; k++){
            proposal++;
            push("researcher.tool_call", "researcher", "Tool call completed.",
              { sub_topic:topics[t], tool:TOOLS[(t + k) % TOOLS.length], proposal_id:"p" + proposal, iteration:k + 1, success:true, error_type:null });
          }
          push("researcher.sub_topic.completed", "researcher", "Sub-topic completed.",
            { sub_topic:topics[t], index:t, stop_reason:"sufficient", iterations:calls, tool_calls:calls, cache_hits: extra ? 1 : 0,
              findings: extra ? 2 : 3, sources_retained: extra ? 2 : 3, elapsed_s: extra ? 41.2 : 68.5 });
        }
        push("researcher.research.completed", "researcher", "Research pass completed.",
          { sub_topics_planned:6, sub_topics_researched:topics.length, sub_topics_skipped:0, findings: extra ? 4 : 18 });
      });
    }
    function evaluator(iteration, extra){
      node("source_evaluator", iteration, function(){
        push("source_evaluator.evaluation.started", "source_evaluator", "Source evaluation started.",
          { finding_count: extra ? 22 : 18, source_count: extra ? 17 : 14 });
        push("source_evaluator.evaluation.completed", "source_evaluator", "Sources scored.",
          { source_count: extra ? 17 : 14, average_score:0.71, low_confidence_count:2, unique_source_count: extra ? 17 : 14,
            scored_count: extra ? 17 : 14, unscored_cap_count:0, unscored_provider_count:0, unscored_missing_count:0,
            reputation_hits:5, reputation_failures:0 });
      });
    }
    function verifier(iteration, extra){
      node("evidence_verifier", iteration, function(){
        push("evidence_verifier.verification.completed", "evidence_verifier", "Findings verified.",
          extra ? { verified:3, verified_corrected:0, quoted:0, dropped:1, context_unchecked:0 }
                : { verified:12, verified_corrected:2, quoted:1, dropped:3, context_unchecked:1 });
      });
    }
    function writer(iteration, draft){
      /* draft 1: the first draft; 2: the redraft; 3: the extra pass's draft */
      node("report_writer", iteration, function(){
        push("report_writer.report.written", "report_writer", "Report drafted.",
          { statements: draft === 2 ? 27 : (draft === 3 ? 31 : 26), citations: draft === 3 ? 34 : 29, refused: draft === 1 ? 2 : 1,
            fact_rows:7, not_found: draft === 3 ? 0 : missing.length, table:"findings", parts:3, failed_parts:0 });
      });
      /* A missing required target the writer discloses under "What we couldn't confirm" is not
         a quality-gate hard failure (agents/quality.py:142-144); the route buys the extra pass
         from missing_required_target_ids (state.py:295-297). */
      var answered = draft === 3 ? targets : targets.filter(function(t){ return missing.indexOf(t) === -1; });
      var stillMissing = draft === 3 ? [] : missing;
      push("graph.quality.assessed", "graph", "The report quality gates found no hard failure.",
        { iteration:iteration, hard_failures:[], required_target_ids:targets,
          answered_target_ids:answered, missing_required_target_ids:stillMissing, uncited_settled_points:0 });
    }
    function reviewer(iteration, decision){
      /* decision: destination, reason, mean_score, material_defects, missing */
      node("report_reviewer", iteration, function(){
        push("graph.report.reviewed", "graph.report_reviewer", "The report was reviewed and scored.",
          { iteration:iteration, review_status:"scored", mean_score:decision.mean_score, material_defects:decision.material_defects,
            reviewed_statements:26, input_fingerprint:"sha256:" + sessionId + "-" + iteration + "-" + decision.destination, reused:false });
        push("graph.route.decided", "graph", "Route decided: " + decision.destination + ".",
          { destination:decision.destination, reason:decision.reason, iteration:iteration, max_extra_passes:budget,
            missing_required_target_ids:decision.missing });
      });
    }
    function finalize(iteration){
      node("finalize_report", iteration, function(){
        push("graph.report.published", "graph.finalize_report", "The final report and its evidence ledger were published.",
          { quality_status:"accepted", report_path:"api-output/report.md", evidence_path:"api-output/report-evidence.md",
            quality_path:"api-output/report-quality.json", document_writes:3, memory_writes:1, error_count:0 });
      });
      push("graph.session.completed", "graph", "Research session finished with status completed.",
        { status:"completed", iteration:iteration, error_count:0, has_report:true });
    }

    push("graph.session.started", "graph", "Research session started.", { session_id:sessionId, max_extra_passes:budget, checkpointing:false });
    node("planner", 0, function(){
      push("planner.planning.completed", "planner", "Plan validated.",
        { sub_topic_count:6, repair_attempted:false, stop_reason:"finished", iterations:1, tool_calls:1 });
    });
    var topics = ["grid connection", "supply chain", "queue reform", "fire codes", "market rules", "financing"];
    researcher(0, topics, false);
    evaluator(0, false);
    verifier(0, false);
    writer(0, 1);

    if(loop === "extra_pass" && budget > 0){
      /* pass 0: two required targets have no verified finding → the graph buys an extra pass
         before acceptance is considered (state.py:295-297) */
      reviewer(0, { destination:"extra_pass", reason:"extra_pass_requested", mean_score:0.82, material_defects:0, missing:missing });
      push("graph.node.started", "graph.extra_pass", "Node extra_pass started.", { node:"extra_pass", iteration:0 });
      push("graph.extra_pass.started", "graph", "Extra pass 1 started.", { iteration:1, max_extra_passes:budget, targets:missing });
      push("graph.node.completed", "graph.extra_pass", "Node extra_pass completed.", { node:"extra_pass", iteration:1, event_count:1, error_count:0 });
      researcher(1, ["supply chain", "financing"], true);
      evaluator(1, true);
      verifier(1, true);
      writer(1, 3);
      reviewer(1, { destination:"finalize", reason:"report_accepted", mean_score:0.86, material_defects:0, missing:[] });
      finalize(1);
    } else if(loop === "redraft"){
      /* pass 0: the review names one material defect → one writer re-run, same iteration (nodes.py:1160-1216) */
      reviewer(0, { destination:"redraft", reason:"redraft_requested", mean_score:0.77, material_defects:1, missing:[] });
      push("graph.node.started", "graph.writer_redraft", "Node writer_redraft started.", { node:"writer_redraft", iteration:0 });
      push("graph.report.redraft_requested", "graph", "Writer re-run 1 requested for one materially defective report.",
        { iteration:0, redrafts:1, material_defects:1 });
      push("graph.node.completed", "graph.writer_redraft", "Node writer_redraft completed.", { node:"writer_redraft", iteration:0, event_count:1, error_count:0 });
      writer(0, 2);
      reviewer(0, { destination:"finalize", reason:"report_accepted", mean_score:0.84, material_defects:0, missing:[] });
      finalize(0);
    } else {
      reviewer(0, { destination:"finalize", reason:"report_accepted", mean_score:0.86, material_defects:0, missing:[] });
      finalize(0);
    }
    return ev;
  }

  /* The halted fixture (2ad900b1): a planner halt. A halting node emits graph.node.started and
     records its error — no graph.node.completed; every later agent node emits graph.node.skipped
     with reason "halted" (nodes.py:175-186, events.py:81-88); the reviewer's route is `end`, so
     finalize_report never runs and no event exists for it; the orchestrator closes with
     status "failed" (orchestrator.py:396-402). Consumed once by the failed-stage renderer,
     never played. Pure literal — the offline key check evaluates it by itself. */
  var HALTED_EVENTS = [
    { type:"graph.session.started", source:"graph", message:"Research session started.", metadata:{ session_id:"2ad900b1", max_extra_passes:1, checkpointing:false } },
    { type:"graph.node.started", source:"graph.planner", message:"Node planner started.", metadata:{ node:"planner", iteration:0 } },
    { type:"graph.node.skipped", source:"graph.researcher", message:"Node researcher was skipped because the run had halted.", metadata:{ node:"researcher", iteration:0, reason:"halted" } },
    { type:"graph.node.skipped", source:"graph.source_evaluator", message:"Node source_evaluator was skipped because the run had halted.", metadata:{ node:"source_evaluator", iteration:0, reason:"halted" } },
    { type:"graph.node.skipped", source:"graph.evidence_verifier", message:"Node evidence_verifier was skipped because the run had halted.", metadata:{ node:"evidence_verifier", iteration:0, reason:"halted" } },
    { type:"graph.node.skipped", source:"graph.report_writer", message:"Node report_writer was skipped because the run had halted.", metadata:{ node:"report_writer", iteration:0, reason:"halted" } },
    { type:"graph.node.skipped", source:"graph.report_reviewer", message:"Node report_reviewer was skipped because the run had halted.", metadata:{ node:"report_reviewer", iteration:0, reason:"halted" } },
    { type:"graph.session.completed", source:"graph", message:"Research session finished with status failed.", metadata:{ status:"failed", iteration:0, error_count:1, has_report:false } }
  ];
```

- [ ] **Step 6: Replace `weigh()` (`:2538-2547`) — key the weights by node, which the old code never did (it looked `W[e.type]` up by event type, so every event weighed 0.9)**

```js
  /* Per-node weighting. Event density is wildly uneven — the researcher emits a tool_call per
     tool per sub-topic while the rest emit a handful of records each — so a wall-clock-
     proportional schedule flashes four steps past in under a second and then sits on the last
     one. Weighting by node gives every step comparable screen time. Pacing only: nothing the
     interface states depends on it. The node is the event's own `node` key, or the agent
     prefix of its type (`researcher.tool_call` → researcher); graph-level events weigh 0.9. */
  function weigh(events){
    var W = {
      planner:0.7, researcher:1.4, source_evaluator:1.1, evidence_verifier:1.2,
      report_writer:1.0, report_reviewer:0.9, finalize_report:0.8
    };
    var totalW = 0, acc = 0;
    events.forEach(function(e){
      var node = (e.metadata && e.metadata.node) || e.type.split(".")[0];
      e.w = W[node] || 0.9; totalW += e.w;
    });
    events.forEach(function(e){ acc += e.w / totalW * 760; e.at = Math.round(acc); });
    return events;
  }
```

- [ ] **Step 7: Replace the comment and code from `/* Scripts are built per session, because how many passes a run takes is a` (`:2549`) through the `}` closing `scriptFor` (`:2561`) — the old comment says the graph loops while the critic recommends it, and it goes with the code, the `currentStageIndex` order list (`:2660`) and `BLURB` (`:2668-2676`)**

```js
  /* Scripts are built per session: which loop a run takes is a property of the run
     (`session.script`), and the budget it was created with is its ceiling. */
  var SCRIPTS = {};
  function scriptFor(session){
    if(!SCRIPTS[session.id]){
      SCRIPTS[session.id] = weigh(buildEvents({ loop: session.script || null, maxExtraPasses: Math.max(0, (session.passes || 1) - 1), sessionId: session.id }));
    }
    return SCRIPTS[session.id];
  }
```

In `currentStageIndex()` replace the `order` array with `var order = AGENT_ORDER;` (Task 4 deletes the function).

```js
  var BLURB = {
    planner:"Turning the question into sub-topics and evidence targets.",
    researcher:"Searching and reading; every finding keeps a verbatim snippet.",
    source_evaluator:"Scoring every source behind the findings.",
    evidence_verifier:"Checking each snippet is on its page, then each figure's context.",
    report_writer:"Drafting from verified findings; every sentence is checked against what it cites.",
    report_reviewer:"Scoring the report; accepted at a mean of 0.80 with no material defect.",
    finalize_report:"Publishing the report, the evidence log and the quality record."
  };
```

- [ ] **Step 8: Run both checks**

Run: `node "$TEMP/dr-probe/check_keys.mjs" docs/design/prototype/index.html "$TEMP/dr-probe/captured.json" --emitted-only`
Expected: `checked 20 emitted event types, 100 emitted keys (emitted only)` then `OK`, exit 0. (The capture holds 21 types; `planner.planning.started` and `planner.memory.recalled` are captured but never emitted, and `graph.node.skipped` is emitted only by `HALTED_EVENTS` and checked against `skipped_keys` — 21 − 2 + 1 = 20. The rule is emitted ⊆ captured.)

Run: `node "$TEMP/dr-probe/probe.mjs" docs/design/prototype index.html "$TEMP/dr-probe/t3-stages.js"`
Expected: exit 0, `"ok": true`.

Run: `rg -n -e 'fact_checker' -e 'synthesizer' -e '\bcritic\b' -e 'graph\.refinement' -e 'destination:\s*"?refine' docs/design/prototype/index.html`
Expected: exactly these survivors, each owned by a later task (line numbers are today's; they shift after Tasks 1–3): the two CSS comments `When the critic sends the report back…` (`:435`, Task 5) and `…the critic can send the report back…` (`:584`, Task 4); the arc block's `loopRows` comment and its `if(id === "critic")` anchor (`:2254`, `:2256`, `:2266`, Task 5); the old `applyEvent()` handlers (`:2612-2645`, Task 4); the `startPlayback` comment `…synthesizer would flash past` (`:2716`, Task 4); and the Limitations line `The refinement budget was exhausted before the critic accepted…` in `populateReport` (`:2902`, Task 7). Anything else is a leftover in this task's own ranges (`STAGES`, `buildEvents`, `weigh`, the `SCRIPTS` comment, `currentStageIndex`'s order list, `BLURB`) — fix it now.

- [ ] **Step 9: Commit**

```bash
git add docs/design/prototype/index.html
git commit -m "design(prototype): seven Evidence Verifier stage rows and a scripted stream with the engine's event names and keys"
```

---

### Task 4: Running stage — the run state machine: active-row rule, counters block, pass number, `drConsole.advanceTo` (spec §4.1 active-row rule, header, counters, spine, AC4/AC5 row and counter clauses)

**Files:**
- Modify: `docs/design/prototype/index.html:1197-1211` (running pipeline card: `<div class="pipe-now">` … `#spineWrap`), `:1223` (`#passSummary` text), `:1214-1221` (the pass-track HTML comment), `:583-589` (the `/* ═══ refinements ═══` CSS comment), `:1033` (append CSS before `</style>`), `:2317-2447` (from `function buildSpine(hostEl){` through the end of `renderSpine` — `SPINE`, `makeLoopLayer`, `loopRows`, `drawLoop`, `setLoopState` above it stay as they are until Task 5), `:2560-2667` (from `var PLAYBACK = scriptFor(SESSIONS[0]);` through the end of `currentStageIndex`), `:2677-2729` (`updateRunningChrome`, `startPlayback`, `stopPlayback`), `:2789-2803` (`finishPlayback`), `:2931` (`renderSpine(document.getElementById("spineFailed"), { planner:"active" }, 0);` in `populateFailed`), `:2976` (`play.iterationShown = null;` in `openSession`), `:3049-3061` (`drConsole`)
- Test: `$TEMP/dr-probe/t4-rows.js`, `$TEMP/dr-probe/t4-burst.js`, `$TEMP/dr-probe/t4-switch.js`, `$TEMP/dr-probe/t4-phone.js`, plus the full `check_keys.mjs` run

**Interfaces:**
- Consumes: `STAGES`, `AGENT_ORDER`, `BLURB`, `buildEvents`, `HALTED_EVENTS`, `scriptFor` (Task 3); `session.final`, `passText`, `renderTopbar`, `COMPOSER_FINAL` (Tasks 1–2).
- Produces: `newRunState(passes) → run` (fields `marks, active, openNode, pass, maxPasses, loop, arc, loopPending, tag, rearmed, rearmedFirst, captions, blurbs, counters, countersPass, finalStatus`), `EVENT_HANDLERS` (table keyed by event type, each `function(run, md)` — the offline check regex-reads `md.<key>` per handler), `applyEvent(run, ev)`, `marksFor(run, activeId)`, `renderSpine(hostEl, marks, run)`, `syncSpine(marks, run)`, `renderCounters(hostId, counters, absentText)`, `COUNTER_ROWS`, `paintRunning(session)`, `updateRunningChrome(session, run)`, `playEvent(ev, session)`, `advanceTo(type) → index|null`, `finishPlayback(session)` (copies `session.final`), `drConsole.advanceTo`. The run state already records `run.loop`, `run.arc` and `run.tag`; Task 5 paints them. Task 6 reuses `newRunState`, `applyEvent`, `marksFor`, `renderSpine`, `renderCounters`.

- [ ] **Step 1: Write the failing rows-and-counters check (the row, header and counter clauses of AC4 and AC5)**

```bash
cat > "$TEMP/dr-probe/t4-rows.js" <<'EOF'
const states = () => $$('#spine li').map((li) => li.dataset.state);
const same = (a, b) => JSON.stringify(a) === JSON.stringify(b);
// the extra-pass run
c.open("8f2c1d90"); await waitFor(() => c.stage() === "running", 3000);
const r1 = c.advanceTo("graph.route.decided");
const atRoute = { states: states(), now: text('#runNow'), stage: text('#runProgressLabel') };
c.advanceTo("graph.node.completed");                 // the reviewer's own completion: inert after a loop decision
const afterCompleted = { states: states(), now: text('#runNow') };
c.advanceTo("graph.extra_pass.started");
const afterHop = { passes: text('#runPasses'), chip: text('#topbarStatus'), caption: text('#spine li[data-stage="researcher"] .stage-meta'),
  blurb: text('#runningBlurb'), countersPass: text('#runCountersPass'),
  thisPass: ['subTopics','findings','verified'].map((k) => text(`#runCounters dd[data-counter="${k}"]`)),
  wholeRun: text('#runCounters dd[data-counter="sources"]'), draft: text('#runCounters dd[data-counter="statements"]'), now: text('#runNow') };
// the redraft run
c.open("c3d7e5f1"); await waitFor(() => c.stage() === "running", 3000);
c.advanceTo("graph.route.decided");
const rAtRoute = { states: states(), now: text('#runNow') };
c.advanceTo("graph.node.completed");
const rAfterCompleted = { states: states() };
c.advanceTo("graph.report.redraft_requested");
const rAfterHop = { passes: text('#runPasses'), draftRows: ['statements','review'].map((k) => text(`#runCounters dd[data-counter="${k}"]`)),
  loopsHidden: $('#spine li[data-stage="report_writer"] .loops').hidden, sources: text('#runCounters dd[data-counter="sources"]') };
// the next graph.node.completed is the writer_redraft hop's; the writer completes again after its report.written
c.advanceTo("report_writer.report.written"); c.advanceTo("graph.node.completed");
const rearmed = { state: $('#spine li[data-stage="report_writer"]').dataset.state, mark: text('#spine li[data-stage="report_writer"] .loops'),
  loopsHidden: $('#spine li[data-stage="report_writer"] .loops').hidden, now: text('#runNow') };
const ok = r1 !== null
  && same(atRoute.states, ["done","active","pending","pending","pending","pending","pending"]) && atRoute.now === "Researching" && atRoute.stage === "stage 2 of 7"
  && same(afterCompleted.states, atRoute.states) && afterCompleted.now === "Researching"
  && afterHop.passes === "pass 2 of 2" && afterHop.chip === "Running · pass 2 of 2" && afterHop.caption === "2 missing targets only"
  && afterHop.blurb === "Researching the 2 targets still missing a verified finding." && afterHop.countersPass === "pass 2"
  && afterHop.thisPass.every((t) => t === "not yet") && afterHop.wholeRun === "14" && afterHop.draft === "26 / 2" && afterHop.now === "Researching"
  && same(rAtRoute.states, ["done","done","done","done","active","pending","pending"]) && rAtRoute.now === "Writing report"
  && same(rAfterCompleted.states, rAtRoute.states)
  && rAfterHop.passes === "pass 1 of 2" && rAfterHop.draftRows.every((t) => t === "not yet") && rAfterHop.loopsHidden === true && rAfterHop.sources === "14"
  && rearmed.state === "loop" && rearmed.mark === "↺" && rearmed.loopsHidden === false && rearmed.now === "Reviewing";
return { ok, atRoute, afterCompleted, afterHop, rAtRoute, rAfterCompleted, rAfterHop, rearmed };
EOF
node "$TEMP/dr-probe/probe.mjs" docs/design/prototype index.html "$TEMP/dr-probe/t4-rows.js"
```

Expected: exit 1 (`c.advanceTo is not a function`).

- [ ] **Step 2: Edit the running pipeline card markup (`:1197-1211`, from `<div class="card stack" style="gap:var(--space-5)" data-od-id="running-pipeline">`)**

```html
          <div class="card stack" style="gap:var(--space-5)" data-od-id="running-pipeline">
            <div class="pipe-now">
              <div>
                <span class="cap">Now</span>
                <div class="now-stage" id="runNow">Planning</div>
                <p class="sm" id="runningBlurb">Turning the question into sub-topics and evidence targets.</p>
              </div>
              <div class="row-between">
                <span class="avail-mono" id="runProgressLabel">stage 1 of 7</span>
                <span class="avail-mono" id="runPasses">pass 1 of 2</span>
              </div>
              <span class="track" id="runTrack" role="progressbar" aria-label="Pipeline progress" aria-valuemin="1" aria-valuemax="7" aria-valuenow="1"><span id="runTrackFill"></span></span>
            </div>
            <!-- Counters (R1): inside the pipeline card, under its header. They update once per
                 node step, because that is how the stream delivers events (orchestrator.py:312-346);
                 a counter whose event has not arrived reads `not yet`, never 0. -->
            <div class="counters" data-od-id="running-counters">
              <div class="row-between">
                <p class="eyebrow" style="margin:0">counted from the event stream</p>
                <span class="avail-mono" id="runCountersPass">pass 1</span>
              </div>
              <dl class="kv kv-2" id="runCounters"></dl>
            </div>
            <div class="spine-wrap" id="spineWrap" data-loop="off">
              <ol class="spine-lg" id="spine" data-od-id="running-spine"></ol>
            </div>
          </div>
```

In the hidden pass-track host (`:1223`) change `<span id="passSummary">pass 1 of 3</span>` to `<span id="passSummary">pass 1 of 2</span>`.

Replace the eight-line HTML comment above that host (`:1214-1221`, `<!-- The refinement track was removed from the interface: …`) with:

```html
          <!-- The pass track was removed from the interface: the running stage shows
               a loop only through the return arcs on the pipeline and its reason in
               the header, and `pass p of P` is the one counter. These two nodes stay
               in the document because the runtime writes to them — keeping them here
               is what would let the track come back without rewiring the event
               handling. Hidden with the existing visually-hidden utility rather than
               a display rule, so nothing a stylesheet says later can bring it back on
               screen by accident. -->
```

- [ ] **Step 3: Append CSS before `</style>` (`:1033`)**

```css
/* ═══ 2026-09-26: the counters block ═══ */
/* A compact two-column key/value list; one column at phone width. */
.counters{padding-bottom:var(--space-5);border-bottom:1px solid var(--border)}
.counters .kv{margin-top:var(--space-3)}
.counters .kv dt{white-space:normal}
.counters .kv dt .cap{display:block;color:var(--meta)}
.kv-2{grid-template-columns:auto minmax(0,1fr) auto minmax(0,1fr)}
@media (max-width:900px){ .kv-2{grid-template-columns:auto minmax(0,1fr)} }
/* An author `display` rule beats the browser's [hidden] rule, so the ↺ span says so itself. */
.spine-lg .loops[hidden]{display:none}
```

Also replace the seven-line CSS comment `/* ═══ refinements ═══ … it is the loop. */` above the pass-track rules (`:583-589`) with:

```css
/* ═══ passes ═══
   No prose. A pass is one full run of the seven steps, and the reviewer's route can
   send the run back — to Researching for an extra pass, to Writing for a redraft — so
   the pipeline legitimately re-arms rows. That is shown rather than described: the
   hidden pass track counts the passes the run may take, the arcs on the pipeline say
   which return is happening right now, and the rows going hollow under them is the
   loop itself. Nothing here explains the loop; it is the loop. */
```

- [ ] **Step 4: Replace `buildSpine` through `renderSpine` (`:2317-2447`, from `function buildSpine(hostEl){` through the `}` closing `renderSpine`)** — `SPINE`, `makeLoopLayer`, `loopRows`, `drawLoop` and `setLoopState` above this range stay untouched in this task:

```js
  function buildSpine(hostEl){
    if(!hostEl) return;
    hostEl.innerHTML = "";
    var rows = [];
    STAGES.forEach(function(s, i){
      var li = document.createElement("li");
      li.setAttribute("data-state", "pending");
      /* the stage id is what the arcs use to find their endpoints, so it is bound to the
         node rather than to its position in the list */
      li.setAttribute("data-stage", s.id);
      li.style.setProperty("--delay", String(i * 60));
      /* No "P1"…"P7" prefix. The bullet already numbers the step, and "P" reads as *pass* —
         which this interface has a separate and real concept for ("pass 1 of 2"). */
      li.innerHTML = '<span class="bullet" aria-hidden="true">' + (i + 1) + "</span>"
        + '<span><span class="stage-name">' + s.label
        + '<span class="sr"></span></span>'
        + '<span class="stage-meta">' + s.meta + "</span>"
        + '<span class="stage-meta loops" hidden></span></span>';
      hostEl.appendChild(li);
      rows.push({
        el: li,
        bullet: li.querySelector(".bullet"),
        name: li.querySelector(".stage-name .sr"),
        meta: li.querySelectorAll(".stage-meta")[0],
        loops: li.querySelector(".loops"),
        state: "pending",
        fed: null
      });
    });
    /* The arc layer belongs to the list it annotates, so it is resolved from the host rather
       than from a global id (the halted-run spine has no wrapper and gets no layer). Exactly one
       layer, always: the previous build's layer is removed before a new one is inserted. */
    var wrap = hostEl.closest ? hostEl.closest(".spine-wrap") : null;
    if(wrap){
      Array.prototype.forEach.call(wrap.querySelectorAll(".loop-layer"), function(old){
        if(old.parentNode) old.parentNode.removeChild(old);
      });
      var svg = makeLoopLayer();
      wrap.insertBefore(svg, hostEl);
      SPINE.svg = svg;
    } else {
      SPINE.svg = null;
    }
    SPINE.host = hostEl;
    SPINE.wrap = wrap;
    SPINE.rows = rows;
    SPINE.layout = { w:0, h:0 };
    drawLoop();
  }

  /* One pointer for the whole list; reset the instant a loop re-arms rows. */
  function resetFlow(){
    if(SPINE.host) SPINE.host.style.setProperty("--base", "0");
  }
  function markFlow(){
    if(SPINE.host) SPINE.host.style.setProperty("--base", "170");
  }

  /* Paint the rows from a marks map (node id → state). `run` supplies the dynamic captions
     and which row carries ↺: the first re-armed row, once it has completed again (§4.1). */
  function syncSpine(marks, run){
    if(!SPINE.rows.length) return;
    var done = function(st){ return st === "done" || st === "loop"; };
    SPINE.rows.forEach(function(row, i){
      var id = STAGES[i].id;
      var st = (marks && marks[id]) || "pending";
      if(st !== row.state){
        row.el.setAttribute("data-state", st);
        row.state = st;
      }
      var prev = i > 0 ? ((marks && marks[STAGES[i - 1].id]) || "pending") : null;
      var fed = prev === null ? null : (done(prev) ? "1" : "0");
      if(fed !== row.fed && fed !== null){
        row.el.setAttribute("data-fed", fed);
        row.fed = fed;
      }
      if(row.name) row.name.textContent = st === "active" ? " (in progress)" : "";
      if(row.meta) row.meta.textContent = (run && run.captions[id]) || STAGES[i].meta;
      if(row.loops){
        var show = !!(run && run.rearmedFirst === id && st === "loop");
        row.loops.hidden = !show;
        row.loops.textContent = show ? "↺" : "";
      }
    });
  }

  /* The pass track stays a visually-hidden host (DESIGN.md §3.5): one dot per pass the run may
     take, filled up to the pass in flight, opening into a ring while a loop is flowing. */
  function renderPassTrack(run){
    var host = document.getElementById("passTrack");
    if(!host || !run) return;
    host.setAttribute("data-refining", run.loop === "flowing" ? "1" : "0");
    host.innerHTML = "";
    for(var i = 0; i < run.maxPasses; i++){
      var el = document.createElement("li");
      el.className = "pass";
      var st = i < run.pass - 1 ? "done" : (i === run.pass - 1 ? "active" : "pending");
      el.setAttribute("data-state", st);
      if(i < run.pass - 1 || (i === run.pass - 1 && run.loop !== "flowing")) el.setAttribute("data-traversed", "1");
      el.style.setProperty("--delay", String(i * 60));
      el.innerHTML = '<span class="dot" aria-hidden="true"></span>'
        + '<span class="pass-n">P' + (i + 1) + "</span>"
        + '<span class="sr">' + (st === "done" ? "complete" : st === "active" ? "running now" : "not reached") + "</span>";
      host.appendChild(el);
    }
    setText("passSummary", "pass " + run.pass + " of " + run.maxPasses);
  }

  function renderSpine(hostEl, marks, run){
    buildSpine(hostEl);
    syncSpine(marks, run);
  }
```

- [ ] **Step 5: Replace the playback state machine (`:2560-2667`, from `var PLAYBACK = scriptFor(SESSIONS[0]);` through the `}` closing `currentStageIndex`)** — keep `scriptFor` (Task 3) and `reducedMotion()` (`:2585-2587`; move it above this block if it sits inside the range). Delete the two `var PLAY_MS = 30000;` lines, `play`, `passes`, `openPass`, `resetPasses`, the old `applyEvent` and `currentStageIndex`, and write:

```js
  var PLAYBACK = null;
  /* Watch time for a scripted run. Sized so every step holds the highlight long
     enough to be read. */
  var PLAY_MS = 30000;
  /* One playback at a time: opening a playback session restarts its script from the
     beginning; the other playback fixture keeps `status: "running"` until it is reopened. */
  var play = { timer:null, idx:0, elapsed:0, session:null, run:null };

  /* ═══ one run, derived from its events and nothing else ═══
     Every handler below is written so the state after event k depends only on events 1..k.
     The real stream delivers a node's events as one burst when the node finishes
     (orchestrator.py:312-346), the prototype plays them one per tick, and a late subscriber
     replays a whole run at once; all three must paint the same screen. */
  function emptyCounters(){
    return { subTopicsDone:null, subTopicsResearched:null, subTopicsTotal:null, toolCalls:null,
             findings:null, sources:null, verified:null, corrected:null, dropped:null,
             statements:null, refused:null, reviewSeen:false, reviewScore:null };
  }
  function newRunState(passes){
    return {
      marks:{},                  /* node id → "done" | "loop" | "skipped"; the active row is derived */
      active:"planner",          /* the "Now" row: the successor of the last graph.node.completed */
      openNode:null,             /* the last graph.node.started with no graph.node.completed — the halting row */
      pass:1, maxPasses:Math.max(1, Number(passes) || 1),
      loop:"off", arc:null,      /* data-loop, data-arc (painted by Task 5's setLoopState) */
      loopPending:false,         /* a loop was routed; the reviewer's own completion is inert */
      tag:null,                  /* { kind, label, text } for the header's loop tag, or null */
      rearmed:{}, rearmedFirst:null,
      captions:{}, blurbs:{},
      counters:emptyCounters(), countersPass:1,
      finalStatus:null
    };
  }
  function nextRow(node){
    var i = AGENT_ORDER.indexOf(node);
    return (i >= 0 && i < AGENT_ORDER.length - 1) ? AGENT_ORDER[i + 1] : null;
  }
  /* Rows fromIndex..5 go hollow and are re-armed: their next completion reads `loop`, and the
     first of them carries ↺. Publishing goes hollow too; the rows before fromIndex keep `done`. */
  function rearm(run, fromIndex){
    run.rearmed = {};
    run.rearmedFirst = AGENT_ORDER[fromIndex];
    for(var i = fromIndex; i <= 5; i++){
      delete run.marks[AGENT_ORDER[i]];
      run.rearmed[AGENT_ORDER[i]] = true;
    }
    delete run.marks.finalize_report;
  }
  function plural(n, one, many){ return n + " " + (n === 1 ? one : many); }

  /* Keyed by event type; each handler reads only `md` (the event's metadata). The offline key
     check reads `md.<key>` out of each body and checks it against a replayed run, so read keys
     only through `md.` and only the keys spec §3.3 lists for that event. */
  var EVENT_HANDLERS = {
    "graph.session.started": function(run, md){
      if(typeof md.max_extra_passes === "number") run.maxPasses = 1 + md.max_extra_passes;
    },
    "graph.node.started": function(run, md){
      run.openNode = md.node;
      /* the pass number is read here and from graph.extra_pass.started only (spec §3.2 rule 1) */
      if(typeof md.iteration === "number") run.pass = md.iteration + 1;
    },
    "graph.node.completed": function(run, md){
      var node = md.node;
      if(run.openNode === node) run.openNode = null;
      if(node === "extra_pass" || node === "writer_redraft") return;          /* hops never map to a row */
      if(node === "report_reviewer" && run.loopPending){ run.loopPending = false; return; }   /* inert after a loop decision */
      run.marks[node] = run.rearmed[node] ? "loop" : "done";
      if(node === "report_reviewer") return;                                  /* the route decision already moved the active row */
      run.active = nextRow(node);
    },
    "graph.node.skipped": function(run, md){
      run.marks[md.node] = "skipped";
      if(run.active === md.node) run.active = null;
    },
    "planner.planning.completed": function(run, md){
      run.captions.planner = plural(md.sub_topic_count, "sub-topic", "sub-topics");
    },
    "researcher.sub_topic.completed": function(run){
      run.counters.subTopicsDone = (run.counters.subTopicsDone || 0) + 1;
    },
    "researcher.tool_call": function(run){
      /* counted only — its `iteration` is the ReAct step index (researcher.py:2612), never the pass */
      run.counters.toolCalls = (run.counters.toolCalls || 0) + 1;
    },
    "researcher.research.completed": function(run, md){
      var c = run.counters;
      c.subTopicsResearched = md.sub_topics_researched;
      c.subTopicsTotal = md.sub_topics_researched + md.sub_topics_skipped;
      c.findings = md.findings;
    },
    "source_evaluator.evaluation.completed": function(run, md){
      run.counters.sources = md.source_count;
    },
    "evidence_verifier.verification.completed": function(run, md){
      var c = run.counters;
      c.verified = md.verified; c.corrected = md.verified_corrected; c.dropped = md.dropped;
    },
    "report_writer.report.written": function(run, md){
      var c = run.counters;
      c.statements = md.statements; c.refused = md.refused;
    },
    "graph.report.reviewed": function(run, md){
      var c = run.counters;
      c.reviewSeen = true;
      c.reviewScore = (typeof md.mean_score === "number") ? md.mean_score : null;
    },
    "graph.route.decided": function(run, md){
      run.tag = null;
      run.loopPending = false;
      if(md.destination === "extra_pass"){
        rearm(run, 1); run.active = "researcher";
        run.loopPending = true; run.arc = "extra_pass"; run.loop = "flowing";
      } else if(md.destination === "redraft"){
        rearm(run, 4); run.active = "report_writer";
        run.loopPending = true; run.arc = "redraft"; run.loop = "flowing";
      } else {
        /* finalize or end: an arc lit by an earlier loop clears here */
        run.loop = "off"; run.arc = null;
        run.active = md.destination === "finalize" ? "finalize_report" : null;
      }
    },
    "graph.extra_pass.started": function(run, md){
      var n = (md.targets || []).length;
      if(typeof md.iteration === "number") run.pass = md.iteration + 1;
      run.loop = "settled";
      run.tag = { kind:"extra_pass", label:"extra pass", text: plural(n, "required target had no verified finding", "required targets had no verified finding") };
      run.captions.researcher = plural(n, "missing target only", "missing targets only");
      run.blurbs.researcher = "Researching the " + plural(n, "target", "targets") + " still missing a verified finding.";
      /* this-pass rows reset; whole-run and current-draft rows keep their values (spec §4.1) */
      var c = run.counters;
      c.subTopicsDone = null; c.subTopicsResearched = null; c.subTopicsTotal = null; c.findings = null;
      c.verified = null; c.corrected = null; c.dropped = null;
      run.countersPass = run.pass;
    },
    "graph.report.redraft_requested": function(run, md){
      run.loop = "settled";
      run.tag = { kind:"redraft", label:"redraft", text: "Reviewer named " + plural(md.material_defects, "material defect", "material defects") };
      /* current-draft rows and the review score reset */
      var c = run.counters;
      c.statements = null; c.refused = null; c.reviewSeen = false; c.reviewScore = null;
    },
    "graph.session.completed": function(run, md){
      run.finalStatus = md.status;
      run.loop = "off"; run.arc = null; run.tag = null;
      run.active = null;
    }
  };
  function applyEvent(run, ev){
    var h = EVENT_HANDLERS[ev.type];
    if(h) h(run, ev.metadata || {});
  }
  /* The marks to paint: the recorded states plus the active row, which is derived. */
  function marksFor(run, activeId){
    var m = {};
    Object.keys(run.marks).forEach(function(k){ m[k] = run.marks[k]; });
    if(activeId && !m[activeId]) m[activeId] = "active";
    return m;
  }

  /* ═══ the counters block ═══ */
  var COUNTER_ROWS = [
    { key:"subTopics",  label:"sub-topics researched", scope:"this pass",
      value:function(c){
        if(c.subTopicsResearched !== null) return c.subTopicsResearched + " of " + c.subTopicsTotal;
        return c.subTopicsDone === null ? null : c.subTopicsDone + " this pass";
      } },
    { key:"toolCalls",  label:"tool calls", scope:"whole run · researcher only",
      value:function(c){ return c.toolCalls === null ? null : String(c.toolCalls); } },
    { key:"findings",   label:"findings", scope:"this pass",
      value:function(c){ return c.findings === null ? null : String(c.findings); } },
    { key:"sources",    label:"sources scored", scope:"whole run",
      value:function(c){ return c.sources === null ? null : String(c.sources); } },
    { key:"verified",   label:"verified / corrected / dropped", scope:"this pass",
      value:function(c){ return c.verified === null ? null : c.verified + " / " + c.corrected + " / " + c.dropped; } },
    { key:"statements", label:"sentences / refused", scope:"current draft",
      value:function(c){ return c.statements === null ? null : c.statements + " / " + c.refused; } },
    { key:"review",     label:"review score", scope:"latest review",
      value:function(c){
        if(!c.reviewSeen) return null;
        return c.reviewScore === null ? { muted:"not scored" } : c.reviewScore.toFixed(2);
      } }
  ];
  /* `absentText` is `not yet` while a run is live and `not reached` after a halt; a value that
     never arrived is text, never 0. */
  function renderCounters(hostId, counters, absentText){
    var host = document.getElementById(hostId);
    if(!host) return;
    host.textContent = "";
    COUNTER_ROWS.forEach(function(row){
      var dt = document.createElement("dt");
      dt.textContent = row.label + " ";
      var sc = document.createElement("span"); sc.className = "cap"; sc.textContent = row.scope; dt.appendChild(sc);
      var dd = document.createElement("dd");
      dd.setAttribute("data-counter", row.key);
      var v = row.value(counters);
      if(v === null){ dd.className = "avail"; dd.textContent = absentText; }
      else if(typeof v === "object"){ dd.className = "avail"; dd.textContent = v.muted; }
      else dd.textContent = v;
      host.appendChild(dt); host.appendChild(dd);
    });
  }
  /* Everything the running stage shows is painted from the run state, here and nowhere else.
     (Task 5 adds the arc and the loop tag to this function.) */
  function paintRunning(session){
    var run = play.run;
    if(!run) return;
    syncSpine(marksFor(run, run.active), run);
    renderCounters("runCounters", run.counters, "not yet");
    setText("runCountersPass", "pass " + run.countersPass);
    renderPassTrack(run);
    updateRunningChrome(session, run);
  }
```

- [ ] **Step 6: Replace `updateRunningChrome`, `startPlayback`, `stopPlayback` (`:2677-2729`) and add `playEvent`/`advanceTo`**

```js
  function updateRunningChrome(session, run){
    var idx = run.active ? AGENT_ORDER.indexOf(run.active) : STAGES.length - 1;
    if(idx < 0) idx = 0;
    var stage = STAGES[idx];
    /* The stage is named once, in the pipeline's own "Now" block. */
    setText("runNow", stage.label);
    setText("runProgressLabel", "stage " + (idx + 1) + " of " + STAGES.length);
    setText("runningBlurb", run.blurbs[stage.id] || BLURB[stage.id] || "Working.");
    setText("runElapsed", fmtElapsed(play.elapsed) + " elapsed");
    setText("runPasses", "pass " + run.pass + " of " + run.maxPasses);
    var fill = document.getElementById("runTrackFill");
    if(fill) fill.style.width = Math.round(((idx + 1) / STAGES.length) * 100) + "%";
    var track = document.getElementById("runTrack");
    if(track) track.setAttribute("aria-valuenow", String(idx + 1));
    /* The topbar chip reads the pass from the session, so the session's zero-based iteration
       and its ceiling follow the run; the chip is rebuilt only when one of them changes. */
    var iteration = run.pass - 1;
    if(session.iteration !== iteration || session.passes !== run.maxPasses){
      session.iteration = iteration;
      session.passes = run.maxPasses;
      renderTopbar();
    }
  }
  /* One event: fold it into the run, then paint. The two `--base` pokes are the fill-wave
     timing (DESIGN.md §3.4) and change nothing the interface states. */
  function playEvent(ev, session){
    if(typeof ev.at === "number") play.elapsed = ev.at;
    applyEvent(play.run, ev);
    var md = ev.metadata || {};
    if(ev.type === "graph.route.decided" && (md.destination === "extra_pass" || md.destination === "redraft")) resetFlow();
    if(ev.type === "graph.node.started" && md.node === "finalize_report") markFlow();
    paintRunning(session);
  }
  function startPlayback(session){
    PLAYBACK = scriptFor(session);
    stopPlayback();
    play.session = session;
    play.idx = 0; play.elapsed = 0;
    play.run = newRunState(session.passes);
    session.status = "running";
    session.iteration = 0;
    buildSpine(document.getElementById("spine"));
    resetFlow();
    setLoopState("off");
    paintRunning(session);
    /* One frame per event on a fixed cadence, so a stage's screen time is its share of the
       schedule rather than its share of the event count. */
    var stepMs = reducedMotion() ? 30 : Math.round(PLAY_MS / PLAYBACK.length);
    play.timer = setInterval(function(){
      if(play.idx >= PLAYBACK.length){ finishPlayback(session); return; }
      playEvent(PLAYBACK[play.idx++], session);
    }, stepMs);
  }
  function stopPlayback(){
    if(play.timer){ clearInterval(play.timer); play.timer = null; }
  }
  /* Review hook: play the active playback session's script synchronously up to and including
     the first event of `type`, then pause. Returns the event's index, or null when the type
     does not occur in the rest of the script (the script is then exhausted and paused). */
  function advanceTo(type){
    var session = play.session;
    if(!session || !PLAYBACK || !play.run) return null;
    stopPlayback();
    while(play.idx < PLAYBACK.length){
      var ev = PLAYBACK[play.idx++];
      playEvent(ev, session);
      if(ev.type === type) return play.idx - 1;
    }
    return null;
  }
```

- [ ] **Step 7: Replace `finishPlayback` (`:2789-2803`); fix the two callers**

```js
  function finishPlayback(session){
    stopPlayback();
    var run = play.run;
    session.status = "completed";
    session.routeReason = "report_accepted";
    /* zero-based, the pass the run ended on; passNumber() adds one at every display site */
    session.iteration = run ? run.pass - 1 : 0;
    if(run) session.passes = run.maxPasses;
    session.finished = new Date().toISOString();
    session.durationSeconds = Math.max(0, Math.round((new Date(session.finished) - new Date(session.started)) / 1000));
    session.reportPath = "api-output/report.md";
    session.evidencePath = "api-output/report-evidence.md";
    session.qualityPath = "api-output/report-quality.json";
    session.trace = "https://smith.langchain.com/o/example/r/" + session.id;
    var fin = session.final || COMPOSER_FINAL;
    session.review = fin.review;
    session.coverage = fin.coverage;
    session.evidenceCounts = fin.evidenceCounts;
    session.playback = false;
    renderList();
    enterReport(session);
  }
```

In `populateFailed()` (`:2931`) change `renderSpine(document.getElementById("spineFailed"), { planner:"active" }, 0);` to `renderSpine(document.getElementById("spineFailed"), { planner:"active" }, null);` (Task 6 rewrites the function). In `openSession()` delete the line `play.iterationShown = null;` (`:2976`) and replace the four-line comment above `session.iteration = 0;` (`:2971-2974`, "Rewind before the first paint…") with `/* Rewind before the first paint: showStage() renders the topbar chip from the session, so a session about to replay from the start must not briefly show the pass it last finished on. */`.

- [ ] **Step 8: Add `advanceTo` to `drConsole` (`:3049-3061`, after `finish`)**

```js
      finish: function(){ var s = activeSession(); if(s) finishPlayback(s); },
      /* 2026-09-26 additions (spec §4.6); Task 5 adds arc() and loop() */
      advanceTo: advanceTo
```

- [ ] **Step 9: Run the rows check and the full key check**

```bash
node "$TEMP/dr-probe/probe.mjs" docs/design/prototype index.html "$TEMP/dr-probe/t4-rows.js"
node "$TEMP/dr-probe/check_keys.mjs" docs/design/prototype/index.html "$TEMP/dr-probe/captured.json"
```

Expected: the probe exits 0 with `"ok": true`; the key check prints `checked 20 emitted event types, 100 emitted keys + handler reads` then `OK`, exit 0.

- [ ] **Step 10: Scoped stale-name check**

Run: `rg -n -e 'fact_checker' -e '\bcritic\b' -e 'synthesizer' -e 'graph\.refinement' -e 'destination:\s*"?refine' -e 'iterationShown' -e 'currentStageIndex' -e 'resetPasses' docs/design/prototype/index.html`
Expected: exactly these survivors, each owned by a later task: the CSS comment `When the critic sends the report back…` above `.spine-wrap` (today `:435`, Task 5); the arc block's `loopRows` comment and its `if(id === "critic")` anchor (today `:2254`, `:2256`, `:2266`, Task 5); and the Limitations line `…before the critic accepted…` in `populateReport` (today `:2902`, Task 7). Nothing else — in particular no `iterationShown`, `currentStageIndex` or `resetPasses`, which this task deleted. (`refining` is not in the pattern: `data-refining` is a live attribute the pass-track CSS at `:632` and the new `renderPassTrack` both use.)

- [ ] **Step 11: Re-run the earlier probes that share this code (`t1-chips.js`, `t2-composer.js`, `t2-zero.js`, `t3-stages.js`) — all exit 0**

`t2-composer.js` still passes because `c.finish()` now goes through the new `finishPlayback`, which keeps the session's settings strip.

- [ ] **Step 12: Review Focus 1 — one whole-run burst paints what per-node bursts paint**

```bash
cat > "$TEMP/dr-probe/t4-burst.js" <<'EOF'
const snap = () => ({ states: $$('#spine li').map((li) => li.dataset.state), now: text('#runNow'), passes: text('#runPasses'),
  counters: $$('#runCounters dd').map((d) => d.textContent.trim()), countersPass: text('#runCountersPass'),
  caption: text('#spine li[data-stage="researcher"] .stage-meta'), mark: $('#spine li[data-stage="researcher"] .loops').hidden });
// one burst: the whole run, up to and including finalize_report's completion
c.open("8f2c1d90"); await waitFor(() => c.stage() === "running", 3000);
c.advanceTo("graph.report.published"); c.advanceTo("graph.node.completed");
const burst = snap();
// per-node bursts, as the stream delivers them: stop at every graph.node.completed and let the page paint between them
c.open("c3d7e5f1"); await waitFor(() => c.stage() === "running", 3000);
c.open("8f2c1d90"); await waitFor(() => c.stage() === "running", 3000);
for (let i = 0; i < 40; i++) { const idx = c.advanceTo("graph.node.completed"); await sleep(20); if (idx === null || $('#spine li[data-stage="finalize_report"]').dataset.state === 'done') break; }
const paced = snap();
const same = (a, b) => JSON.stringify(a) === JSON.stringify(b);
const ok = same(burst, paced)
  && same(burst.states, ["done","loop","loop","loop","loop","loop","done"])
  && burst.now === "Publishing" && burst.passes === "pass 2 of 2" && burst.countersPass === "pass 2"
  && burst.mark === false && burst.caption === "2 missing targets only"
  && same(burst.counters, ["2 of 2", "22", "4", "17", "3 / 0 / 1", "31 / 1", "0.86"]);
return { ok, burst, paced };
EOF
node "$TEMP/dr-probe/probe.mjs" docs/design/prototype index.html "$TEMP/dr-probe/t4-burst.js"
```

Expected: exit 0, `"ok": true` (Reviewing reads `loop`, never a stale `done` above hollow rows; tool calls 6×3 + 2×2 = 22).

- [ ] **Step 13: Review Focus 5 — switching between the two playback fixtures (state)**

```bash
cat > "$TEMP/dr-probe/t4-switch.js" <<'EOF'
c.open("8f2c1d90"); await waitFor(() => c.stage() === "running", 3000);
c.advanceTo("graph.extra_pass.started");
const a = { passes: text('#runPasses'), caption: text('#spine li[data-stage="researcher"] .stage-meta') };
c.open("c3d7e5f1"); await waitFor(() => c.stage() === "running", 3000);
const b = { passes: text('#runPasses'), states: $$('#spine li').map((li) => li.dataset.state), caption: text('#spine li[data-stage="researcher"] .stage-meta'),
  counters: $$('#runCounters dd').map((d) => d.textContent.trim()), countersPass: text('#runCountersPass'), chip: text('#topbarStatus'),
  other: c.sessions.find((s) => s.id === "8f2c1d90").status };
c.open("8f2c1d90"); await waitFor(() => c.stage() === "running", 3000);
const d = { passes: text('#runPasses'), states: $$('#spine li').map((li) => li.dataset.state), now: text('#runNow') };
const ok = a.passes === "pass 2 of 2" && a.caption === "2 missing targets only"
  && b.passes === "pass 1 of 2" && b.states[0] === "active" && b.states.slice(1).every((s) => s === "pending") && b.caption === "search · scrape · read · memory"
  && b.counters.every((t) => t === "not yet") && b.countersPass === "pass 1" && b.chip === "Running · pass 1 of 2" && b.other === "running"
  && d.passes === "pass 1 of 2" && d.states[0] === "active" && d.now === "Planning";
return { ok, a, b, d };
EOF
node "$TEMP/dr-probe/probe.mjs" docs/design/prototype index.html "$TEMP/dr-probe/t4-switch.js"
```

Expected: exit 0, `"ok": true`.

- [ ] **Step 14: The counters block at phone width**

```bash
cat > "$TEMP/dr-probe/t4-phone.js" <<'EOF'
c.open("8f2c1d90"); await waitFor(() => c.stage() === "running", 3000);
c.advanceTo("graph.node.completed");
const counters = $('#runCounters');
const cols = getComputedStyle(counters).gridTemplateColumns.split(' ').length;
return { ok: counters.scrollWidth <= counters.clientWidth && cols === (innerWidth <= 900 ? 2 : 4) && document.scrollingElement.scrollWidth <= innerWidth,
         cols, innerWidth, scroll: [counters.scrollWidth, counters.clientWidth] };
EOF
node "$TEMP/dr-probe/probe.mjs" docs/design/prototype index.html "$TEMP/dr-probe/t4-phone.js"
node "$TEMP/dr-probe/probe.mjs" docs/design/prototype index.html "$TEMP/dr-probe/t4-phone.js" --viewport=390x844
```

Expected: both exit 0 (`cols` is 4 at 1252 and 2 at 390).

- [ ] **Step 15: Commit**

```bash
git add docs/design/prototype/index.html
git commit -m "design(prototype): running stage derives rows, pass number and counters from the event stream; drConsole.advanceTo"
```

---

### Task 5: Running stage — two arcs, the loop tag, `drConsole.arc/loop` (spec §4.1 arcs and loop tag, Q3, AC4/AC5 arc clauses, AC6 running half)

**Files:**
- Modify: `docs/design/prototype/index.html` — the running header block (`<p class="sm" id="runningBlurb">…` inside `.pipe-now`, Task 4's markup), `:1033` (append CSS before `</style>`), `:434-443` (the `/* ═══ the refinement loop ═══` CSS comment), the arc block `var SPINE = { host:null, wrap:null, svg:null, rows:[], layout:{w:0,h:0} };` through the end of `setLoopState` (today `:2236-2315`), `paintRunning` and `startPlayback` (Task 4), `drConsole`
- Test: `$TEMP/dr-probe/t5-extra.js`, `$TEMP/dr-probe/t5-redraft.js`, `$TEMP/dr-probe/t5-zero.js`, `$TEMP/dr-probe/t5-reduced.js`, `$TEMP/dr-probe/t5-switch.js`, `$TEMP/dr-probe/t5-burst.js`

**Interfaces:**
- Consumes: `run.loop`, `run.arc`, `run.tag`, `paintRunning`, `startPlayback`, `advanceTo` (Task 4); `SPINE`, `makeLoopLayer`, `resetFlow`, `buildSpine` (Task 4 / existing).
- Produces: `ARCS` (`{ extra_pass:{from:"report_reviewer", to:"researcher"}, redraft:{from:"report_reviewer", to:"report_writer"} }`), `loopRows(from, to) → {from,to}|null`, `drawLoop()` (zero-argument, as the resize handler at `:1671` calls it), `setLoopState(loopState, arcKey)`, `renderLoopTag(run)`, `#spineWrap[data-arc]`, `#runLoopTag`, `drConsole.arc()`, `drConsole.loop()`. Task 10's `09-running-extra-pass` capture asserts `arc()` and `loop()`.

- [ ] **Step 1: Write the failing AC4 arc check (extra pass)**

```bash
cat > "$TEMP/dr-probe/t5-extra.js" <<'EOF'
const rgb = (v) => getComputedStyle(document.documentElement).getPropertyValue(v).trim();
const toRgb = (hex) => { const n = parseInt(hex.slice(1), 16); return `rgb(${n >> 16}, ${(n >> 8) & 255}, ${n & 255})`; };
c.open("8f2c1d90"); await waitFor(() => c.stage() === "running", 3000);
const before = { loop: c.loop(), arc: c.arc(), tagHidden: $('#runLoopTag').hidden };
c.advanceTo("graph.route.decided");
const atRoute = { loop: c.loop(), arc: c.arc(), wrap: [$('#spineWrap').dataset.loop, $('#spineWrap').dataset.arc],
  stroke: getComputedStyle($('#spineWrap .loop-flow')).stroke, base: getComputedStyle($('#spineWrap .loop-base')).stroke, head: getComputedStyle($('#spineWrap .loop-head')).fill,
  tagHidden: $('#runLoopTag').hidden };
// the path starts at the report_reviewer bullet's centre and its vertical ends at the researcher bullet's row
const d = $('#spineWrap .loop-base').getAttribute('d');
const wrapBox = $('#spineWrap').getBoundingClientRect();
const bullet = (id) => { const b = $(`#spine li[data-stage="${id}"] .bullet`).getBoundingClientRect(); return { x: b.left - wrapBox.left + b.width / 2, y: b.top - wrapBox.top + b.height / 2 }; };
const from = bullet('report_reviewer'), to = bullet('researcher');
const m = /^M ([\d.]+) ([\d.]+) H [\d.]+ V ([\d.]+) H/.exec(d) || [];
const anchored = Math.abs(+m[1] - from.x) < 1 && Math.abs(+m[2] - from.y) < 1 && Math.abs(+m[3] - to.y) < 1;
c.advanceTo("graph.node.completed");                 // the reviewer's own completion
const afterCompleted = { loop: c.loop(), arc: c.arc() };
c.advanceTo("graph.extra_pass.started");
const afterHop = { loop: c.loop(), arc: c.arc(), tagHidden: $('#runLoopTag').hidden, tagKind: $('#runLoopTag').dataset.kind,
  tag: text('#runLoopTag .tag'), why: text('#runLoopTag .why'), tagColor: getComputedStyle($('#runLoopTag .tag')).color,
  tagDisplay: getComputedStyle($('#runLoopTag')).display };
c.advanceTo("graph.route.decided");                  // pass 1's acceptance clears the arc and the tag
const afterAccept = { loop: c.loop(), arc: c.arc(), tagHidden: $('#runLoopTag').hidden, tagDisplay: getComputedStyle($('#runLoopTag')).display, hasArcAttr: $('#spineWrap').hasAttribute('data-arc') };
const ok = before.loop === "off" && before.arc === null && before.tagHidden === true
  && atRoute.loop === "flowing" && atRoute.arc === "extra_pass" && atRoute.wrap[0] === "flowing" && atRoute.wrap[1] === "extra_pass"
  && atRoute.stroke === toRgb(rgb('--warn')) && atRoute.base === toRgb(rgb('--warn')) && atRoute.head === toRgb(rgb('--warn')) && anchored && atRoute.tagHidden === true
  && afterCompleted.loop === "flowing" && afterCompleted.arc === "extra_pass"
  && afterHop.loop === "settled" && afterHop.arc === "extra_pass" && afterHop.tagHidden === false && afterHop.tagKind === "extra_pass"
  && afterHop.tag === "extra pass" && afterHop.why === "2 required targets had no verified finding" && afterHop.tagDisplay !== "none"
  && afterHop.tagColor !== toRgb(rgb('--warn'))     // text is --status-warn (oklch), never the arc's --warn
  && afterAccept.loop === "off" && afterAccept.arc === null && afterAccept.tagHidden === true && afterAccept.tagDisplay === "none" && afterAccept.hasArcAttr === false;
return { ok, before, atRoute, anchored, afterCompleted, afterHop, afterAccept };
EOF
node "$TEMP/dr-probe/probe.mjs" docs/design/prototype index.html "$TEMP/dr-probe/t5-extra.js"
```

Expected: exit 1 (`c.loop is not a function`).

- [ ] **Step 2: Write the failing AC5 arc check (redraft)**

```bash
cat > "$TEMP/dr-probe/t5-redraft.js" <<'EOF'
const rgb = (v) => getComputedStyle(document.documentElement).getPropertyValue(v).trim();
const toRgb = (hex) => { const n = parseInt(hex.slice(1), 16); return `rgb(${n >> 16}, ${(n >> 8) & 255}, ${n & 255})`; };
c.open("c3d7e5f1"); await waitFor(() => c.stage() === "running", 3000);
c.advanceTo("graph.route.decided");
const atRoute = { arc: c.arc(), loop: c.loop(), stroke: getComputedStyle($('#spineWrap .loop-flow')).stroke };
const d = $('#spineWrap .loop-base').getAttribute('d');
const wrapBox = $('#spineWrap').getBoundingClientRect();
const bullet = (id) => { const b = $(`#spine li[data-stage="${id}"] .bullet`).getBoundingClientRect(); return { x: b.left - wrapBox.left + b.width / 2, y: b.top - wrapBox.top + b.height / 2 }; };
const from = bullet('report_reviewer'), to = bullet('report_writer');
const m = /^M ([\d.]+) ([\d.]+) H [\d.]+ V ([\d.]+) H/.exec(d) || [];
const anchored = Math.abs(+m[1] - from.x) < 1 && Math.abs(+m[2] - from.y) < 1 && Math.abs(+m[3] - to.y) < 1;
c.advanceTo("graph.node.completed");
const afterCompleted = { arc: c.arc(), loop: c.loop() };
c.advanceTo("graph.report.redraft_requested");
const afterHop = { loop: c.loop(), arc: c.arc(), tagKind: $('#runLoopTag').dataset.kind, tag: text('#runLoopTag .tag'), why: text('#runLoopTag .why'),
  tagColor: getComputedStyle($('#runLoopTag .tag')).color, tagBorder: getComputedStyle($('#runLoopTag .tag')).borderColor, passes: text('#runPasses') };
const ok = atRoute.arc === "redraft" && atRoute.loop === "flowing" && atRoute.stroke === toRgb(rgb('--meta')) && anchored
  && afterCompleted.arc === "redraft" && afterCompleted.loop === "flowing"
  && afterHop.loop === "settled" && afterHop.arc === "redraft" && afterHop.tagKind === "redraft" && afterHop.tag === "redraft"
  && /^Reviewer named 1 material defect$/.test(afterHop.why) && afterHop.passes === "pass 1 of 2"
  && afterHop.tagBorder === toRgb(rgb('--meta')) && afterHop.tagColor !== toRgb(rgb('--meta'));   // --meta is a border/stroke, never text
return { ok, atRoute, anchored, afterCompleted, afterHop };
EOF
```

- [ ] **Step 3: Write the failing AC6 running-half check (composer run at Extra passes 0)**

```bash
cat > "$TEMP/dr-probe/t5-zero.js" <<'EOF'
$('#plusBtn').click(); await sleep(50); $('#extraMinus').click(); $('#popClose').click();
c.submit("How reliable are consumer-grade air quality sensors?");
await waitFor(() => c.stage() === "running", 6000);
const seenLoop = new Set(), seenChip = new Set();
const wrap = $('#spineWrap');
const mo = new MutationObserver(() => seenLoop.add(wrap.dataset.loop));
mo.observe(wrap, { attributes: true, attributeFilter: ['data-loop', 'data-arc'] });
seenLoop.add(wrap.dataset.loop); seenChip.add(text('#topbarStatus'));
for (const t of ["graph.node.completed", "graph.route.decided", "graph.report.published", "graph.session.completed"]) { c.advanceTo(t); await sleep(0); seenLoop.add(wrap.dataset.loop); seenChip.add(text('#topbarStatus')); }
mo.disconnect();
const last = { passes: text('#runPasses'), states: $$('#spine li').map((li) => li.dataset.state), arc: c.arc(), tagHidden: $('#runLoopTag').hidden };
return { ok: [...seenLoop].every((v) => v === "off") && [...seenChip].every((v) => v === "Running · pass 1 of 1") && last.passes === "pass 1 of 1"
             && JSON.stringify(last.states) === JSON.stringify(["done","done","done","done","done","done","done"]) && last.arc === null && last.tagHidden === true,
         seenLoop: [...seenLoop], seenChip: [...seenChip], last };
EOF
```

- [ ] **Step 4: Add the loop tag to the header block** — inside `.pipe-now`, directly after `<p class="sm" id="runningBlurb">…</p>`:

```html
                <!-- The loop's reason, shown while a loop is taken (Q3). Hidden when none. -->
                <p class="loop-tag" id="runLoopTag" hidden><span class="tag"></span><span class="why"></span></p>
```

- [ ] **Step 5: Append CSS before `</style>`**

```css
/* ═══ 2026-09-26: two arcs and the loop tag ═══ */
/* The arc's stroke is the loop's kind: amber `--warn` for an extra pass, grey `--meta` for a
   redraft (spec §4.1). `--meta` is a stroke here and never text. */
.spine-wrap[data-arc="extra_pass"] .loop-layer .loop-base,
.spine-wrap[data-arc="extra_pass"] .loop-layer .loop-flow{stroke:var(--warn)}
.spine-wrap[data-arc="extra_pass"] .loop-layer .loop-head{fill:var(--warn)}
.spine-wrap[data-arc="redraft"] .loop-layer .loop-base,
.spine-wrap[data-arc="redraft"] .loop-layer .loop-flow{stroke:var(--meta)}
.spine-wrap[data-arc="redraft"] .loop-layer .loop-head{fill:var(--meta)}
/* The loop tag: a small bordered word plus the reason. Text amber is the text-safe
   `--status-warn` (the same `.pop-note.warn` uses), never `--warn`. An author `display`
   rule beats the browser's [hidden] rule, so the tag says so itself. */
.loop-tag{display:flex;align-items:center;gap:var(--space-2);flex-wrap:wrap;font-size:var(--text-sm);color:var(--muted);margin-top:var(--space-2)}
.loop-tag[hidden]{display:none}
.loop-tag .tag{font-family:var(--font-mono);font-size:var(--text-xs);border:1px solid var(--border);border-radius:var(--radius-sm);padding:1px 6px;color:var(--fg)}
.loop-tag[data-kind="extra_pass"] .tag{color:var(--status-warn);border-color:var(--status-warn)}
.loop-tag[data-kind="redraft"] .tag{color:var(--muted);border-color:var(--meta)}
```

Also replace the ten-line CSS comment `/* ═══ the refinement loop ═══ … to the nodes rather than to a guessed geometry. */` above `.spine-wrap{position:relative}` (`:434-443`) with:

```css
/* ═══ the two return arcs ═══
   When the reviewer routes a loop, the flow leaves Reviewing and returns to
   Researching (an extra pass) or to Writing (a redraft). That jump was the confusing
   part: a linear list cannot show a jump backwards, and a reset pipeline on its own
   looks like progress was lost. So the jump is drawn — a path down the left gutter
   from the Reviewing node back up to the destination node, with a dashed overlay that
   travels along it, stroked amber for an extra pass and grey for a redraft.

   The overlay animates only during the handoff (`data-loop="flowing"`), then rests
   lit while the re-armed rows run and clears when the next route is decided or the
   run ends. The arc is measured from the real node positions on every layout pass,
   so it stays attached to the nodes rather than to a guessed geometry. */
```

- [ ] **Step 6: Replace the arc block (`var SPINE = { host:null, wrap:null, svg:null, rows:[], layout:{w:0,h:0} };` through the `}` closing `setLoopState`, today `:2236-2315`)**

```js
  var SPINE = { host:null, wrap:null, svg:null, rows:[], layout:{w:0,h:0}, arc:null };

  /* The SVG uses the container's pixel geometry as its own coordinate system, so
     no viewBox is needed and nothing scales or letterboxes. */
  function makeLoopLayer(){
    var NS = "http://www.w3.org/2000/svg";
    var svg = document.createElementNS(NS, "svg");
    svg.setAttribute("class", "loop-layer");
    svg.setAttribute("aria-hidden", "true");
    ["loop-base", "loop-flow", "loop-head"].forEach(function(cls){
      var p = document.createElementNS(NS, "path");
      p.setAttribute("class", cls);
      svg.appendChild(p);
    });
    return svg;
  }

  /* Two arcs, both leaving Reviewing (the route decision is the reviewer's, nodes.py:819-841):
     the extra pass returns to Researching (extra_pass → researcher, orchestrator.py:206) and
     the redraft returns to Writing (writer_redraft → report_writer, :210). Publishing is in
     neither loop. Endpoints are resolved from each row's data-stage, never from list position. */
  var ARCS = {
    extra_pass: { from:"report_reviewer", to:"researcher"    },
    redraft:    { from:"report_reviewer", to:"report_writer" }
  };
  function loopRows(from, to){
    var pair = { from:null, to:null };
    SPINE.rows.forEach(function(row){
      var id = row.el.getAttribute("data-stage");
      if(id === from) pair.from = row;
      if(id === to) pair.to = row;
    });
    return (pair.from && pair.to) ? pair : null;
  }

  function drawLoop(){
    if(!SPINE.wrap || !SPINE.svg || SPINE.rows.length < 2) return;
    var arc = ARCS[SPINE.arc];
    if(!arc) return;
    var pair = loopRows(arc.from, arc.to);
    if(!pair) return;
    var hostRect = SPINE.wrap.getBoundingClientRect();
    var w = Math.round(hostRect.width), h = Math.round(hostRect.height);
    if(!w || !h) return;
    if(SPINE.layout.w !== w || SPINE.layout.h !== h){
      SPINE.layout = { w:w, h:h };
      SPINE.svg.setAttribute("width", w);
      SPINE.svg.setAttribute("height", h);
    }
    var leave = pair.from.bullet.getBoundingClientRect();   /* where the loop leaves */
    var enter = pair.to.bullet.getBoundingClientRect();     /* where it returns */
    var x1 = enter.left - hostRect.left + enter.width / 2;
    var y1 = enter.top  - hostRect.top  + enter.height / 2;
    var x2 = leave.left - hostRect.left + leave.width / 2;
    var y2 = leave.top  - hostRect.top  + leave.height / 2;
    /* the vertical runs in the container's padding, left of every row, so rows
       stay fully clickable and the arc never crosses a label */
    var r = Math.max(2, x1 - enter.width / 2 - 8);
    var d = "M " + x2 + " " + y2 + " H " + r + " V " + y1 + " H " + (x1 + 10);
    var base = SPINE.svg.querySelector(".loop-base");
    var flow = SPINE.svg.querySelector(".loop-flow");
    var head = SPINE.svg.querySelector(".loop-head");
    if(base) base.setAttribute("d", d);
    if(flow) flow.setAttribute("d", d);
    /* arrowhead pointing into the destination row */
    if(head) head.setAttribute("d", "M " + (x1 + 3) + " " + (y1 - 4)
      + " L " + (x1 + 11) + " " + y1
      + " L " + (x1 + 3) + " " + (y1 + 4) + " Z");
  }

  /* Three states for an arc: off while a run has not looped, flowing during the handoff
     (dashes travel from Reviewing to the destination), settled while the re-armed rows run.
     `data-arc` names which of the two arcs is lit; at most one ever is. */
  function setLoopState(v, arc){
    var wrap = document.getElementById("spineWrap");
    if(!wrap) return;
    if(wrap.getAttribute("data-loop") !== v) wrap.setAttribute("data-loop", v);
    var a = arc || "";
    if((wrap.getAttribute("data-arc") || "") !== a){
      if(a) wrap.setAttribute("data-arc", a); else wrap.removeAttribute("data-arc");
    }
    SPINE.arc = arc || null;
    if(v !== "off") drawLoop();
  }
```

- [ ] **Step 7: Paint the arc and the tag; add the hooks**

Add after `renderCounters` (Task 4):

```js
  function renderLoopTag(run){
    var el = document.getElementById("runLoopTag");
    if(!el) return;
    if(!run.tag){ el.hidden = true; el.removeAttribute("data-kind"); return; }
    el.hidden = false;
    el.setAttribute("data-kind", run.tag.kind);
    el.querySelector(".tag").textContent = run.tag.label;
    el.querySelector(".why").textContent = run.tag.text;
  }
```

In `paintRunning` (Task 4) insert two lines after `syncSpine(marksFor(run, run.active), run);`:

```js
    setLoopState(run.loop, run.arc);
    renderLoopTag(run);
```

and replace its leading comment with `/* Everything the running stage shows is painted from the run state, here and nowhere else. */`. In `startPlayback` change `setLoopState("off");` to `setLoopState("off", null);`. In `drConsole`, change the last member `advanceTo: advanceTo` to `advanceTo: advanceTo,` (the trailing comma is what lets the next members follow) and add after it:

```js
      arc: function(){
        var wrap = document.getElementById("spineWrap");
        if(!wrap || wrap.getAttribute("data-loop") === "off") return null;
        return wrap.getAttribute("data-arc") || null;
      },
      loop: function(){
        var wrap = document.getElementById("spineWrap");
        return wrap ? wrap.getAttribute("data-loop") : null;
      }
```

- [ ] **Step 8: Run the three checks; then Review Focus 1 and 5 (arc and tag parts) and reduced motion**

```bash
node "$TEMP/dr-probe/probe.mjs" docs/design/prototype index.html "$TEMP/dr-probe/t5-extra.js"
node "$TEMP/dr-probe/probe.mjs" docs/design/prototype index.html "$TEMP/dr-probe/t5-redraft.js"
node "$TEMP/dr-probe/probe.mjs" docs/design/prototype index.html "$TEMP/dr-probe/t5-zero.js"
```

Expected: all exit 0 with `"ok": true`.

```bash
cat > "$TEMP/dr-probe/t5-burst.js" <<'EOF'
const snap = () => ({ loop: c.loop(), arc: c.arc(), tagHidden: $('#runLoopTag').hidden, hasArcAttr: $('#spineWrap').hasAttribute('data-arc') });
c.open("8f2c1d90"); await waitFor(() => c.stage() === "running", 3000);
c.advanceTo("graph.report.published"); c.advanceTo("graph.node.completed");
const burst = snap();
c.open("c3d7e5f1"); await waitFor(() => c.stage() === "running", 3000);
c.open("8f2c1d90"); await waitFor(() => c.stage() === "running", 3000);
for (let i = 0; i < 40; i++) { const idx = c.advanceTo("graph.node.completed"); await sleep(20); if (idx === null || $('#spine li[data-stage="finalize_report"]').dataset.state === 'done') break; }
const paced = snap();
return { ok: JSON.stringify(burst) === JSON.stringify(paced) && burst.loop === "off" && burst.arc === null && burst.tagHidden === true && burst.hasArcAttr === false, burst, paced };
EOF
cat > "$TEMP/dr-probe/t5-switch.js" <<'EOF'
c.open("8f2c1d90"); await waitFor(() => c.stage() === "running", 3000);
c.advanceTo("graph.extra_pass.started");
const a = { arc: c.arc(), loop: c.loop() };
c.open("c3d7e5f1"); await waitFor(() => c.stage() === "running", 3000);
const b = { arc: c.arc(), loop: c.loop(), tagHidden: $('#runLoopTag').hidden, hasArcAttr: $('#spineWrap').hasAttribute('data-arc') };
c.open("8f2c1d90"); await waitFor(() => c.stage() === "running", 3000);
const d = { arc: c.arc(), loop: c.loop(), tagHidden: $('#runLoopTag').hidden };
return { ok: a.arc === "extra_pass" && a.loop === "settled" && b.arc === null && b.loop === "off" && b.tagHidden === true && b.hasArcAttr === false
             && d.arc === null && d.loop === "off" && d.tagHidden === true, a, b, d };
EOF
cat > "$TEMP/dr-probe/t5-reduced.js" <<'EOF'
c.open("8f2c1d90"); await waitFor(() => c.stage() === "running", 3000);
c.advanceTo("graph.extra_pass.started"); await sleep(50);
const layer = getComputedStyle($('#spineWrap .loop-layer')), flow = getComputedStyle($('#spineWrap .loop-flow'));
return { ok: c.motion() === "reduced" && layer.opacity === "1" && flow.animationName === "none" && c.arc() === "extra_pass" && c.loop() === "settled",
         motion: c.motion(), opacity: layer.opacity, animation: flow.animationName };
EOF
node "$TEMP/dr-probe/probe.mjs" docs/design/prototype index.html "$TEMP/dr-probe/t5-burst.js"
node "$TEMP/dr-probe/probe.mjs" docs/design/prototype index.html "$TEMP/dr-probe/t5-switch.js"
node "$TEMP/dr-probe/probe.mjs" docs/design/prototype index.html "$TEMP/dr-probe/t5-reduced.js" --reduced-motion
```

Expected: all exit 0 (under reduced motion both arcs arrive already lit: `.loop-layer` opacity `1`, no dash animation).

- [ ] **Step 9: Scoped stale-name check, re-run Task 4's probes, commit**

Run: `rg -n -e '\bcritic\b' -e 'refinement loop' -e 'now refining' docs/design/prototype/index.html`
Expected: exactly one survivor — the Limitations line `…before the critic accepted…` in `populateReport` (today `:2902`), which Task 7 removes. The arc comment and the `loopRows` block this task replaced no longer match.

Re-run `t4-rows.js`, `t4-burst.js` and `t4-switch.js` — all still exit 0 — then commit:

```bash
git add docs/design/prototype/index.html
git commit -m "design(prototype): two return arcs (extra pass amber, redraft grey) with the loop's reason in the header; drConsole.arc/loop"
```

---

### Task 6: Failed stage — plain-words headline, skipped rows from `HALTED_EVENTS`, survived counters (spec §4.2, AC10)

**Files:**
- Modify: `docs/design/prototype/index.html:1340-1390` (`<!-- ── STAGE · failed` section: failed panel, facts card, rail), `function populateFailed(session){` (today `:2908-2932`)
- Test: `$TEMP/dr-probe/t6-failed.js`

**Interfaces:**
- Consumes: `HALTED_EVENTS` (Task 3); `newRunState`, `applyEvent`, `marksFor`, `renderSpine`, `renderCounters`, `AGENT_ORDER` (Task 4).
- Produces: `HALT_HEADLINES` map; `populateFailed(session)` derives the failed spine and the survived counters from `HALTED_EVENTS` through the same handlers as the running stage; elements `#failedHaltedAt`, `#failedCounters`.

- [ ] **Step 1: Write the failing check**

```bash
cat > "$TEMP/dr-probe/t6-failed.js" <<'EOF'
c.open("2ad900b1"); await waitFor(() => c.stage() === "failed", 3000);
const states = $$('#spineFailed li').map((li) => [li.dataset.stage, li.dataset.state]);
const facts = $$('#stage-failed dl.kv dd').map((d) => d.textContent.trim());
const counters = $$('#failedCounters dd').map((d) => [d.textContent.trim(), d.className]);
const downloads = $$('#stage-failed a, #stage-failed button').filter((el) => /download/i.test(el.textContent)).length;
const zeros = $$('#stage-failed dd').filter((d) => d.textContent.trim() === '0').length;
const ok = text('#failedType') === 'Model provider misconfigured' && facts.includes('graph_provider_configuration_error')
  && JSON.stringify(states) === JSON.stringify([["planner","active"],["researcher","skipped"],["source_evaluator","skipped"],["evidence_verifier","skipped"],
       ["report_writer","skipped"],["report_reviewer","skipped"],["finalize_report","skipped"]])
  && counters.length === 7 && counters.every(([t, cls]) => t === 'not reached' && cls === 'avail')
  && downloads === 0 && zeros === 0 && !/claims checked/.test($('#stage-failed').textContent)
  && text('#failedHaltedAt') === 'halted at stage 1' && $('#stage-failed').textContent.includes('A halted run skips publication: no report, evidence log or quality record was written.');
return { ok, headline: text('#failedType'), states, facts, counters, downloads, zeros, haltedAt: text('#failedHaltedAt') };
EOF
node "$TEMP/dr-probe/probe.mjs" docs/design/prototype index.html "$TEMP/dr-probe/t6-failed.js"
```

Expected: exit 1 (`headline` is `graph_provider_configuration_error`; rows 2–7 are `pending`; three literal `0`s).

- [ ] **Step 2: Rewrite the failed-stage body markup (`:1355-1390`, from `<div class="with-rail">` inside `#stage-failed` to its closing `</div>`)**

```html
        <div class="with-rail">
          <div class="stack" style="gap:var(--space-5)">
            <div class="note bad" data-od-id="failed-panel">
              <!-- The headline is the halting type in plain words (spec §4.2); the enumerated
                   error_type stays in the facts list and the API's own message is the sentence. -->
              <div class="note-head"><span class="mk">halt</span><span id="failedType">—</span></div>
              <p id="failedMessage">The run stopped on a non-recoverable error.</p>
            </div>

            <div class="card stack" style="gap:var(--space-4)" data-od-id="failed-facts">
              <h2 class="card-title">Why nothing was published</h2>
              <p class="sm">A halted run skips publication: no report, evidence log or quality record was written.</p>
              <dl class="kv">
                <dt>error_type</dt><dd id="failFactType">—</dd>
                <dt>source</dt><dd id="failFactSource">—</dd>
                <dt>recoverable</dt><dd id="failFactRecoverable">false</dd>
                <dt>report_path</dt><dd class="avail">Not published</dd>
                <dt>GET /report</dt><dd>409 report_unavailable</dd>
              </dl>
              <p class="avail">A finished session with no artifact answers <span class="mono">409 report_unavailable</span> — distinct from the <span class="mono">409 session_not_complete</span> a running session returns, and from the <span class="mono">404</span> an unknown id returns. There is no download control here, disabled or otherwise.</p>
            </div>
          </div>

          <aside class="rail" data-od-id="failed-rail" aria-label="Run details">
            <div class="card stack" style="gap:var(--space-4)">
              <div class="row-between">
                <h2 class="card-title">Where it stopped</h2>
                <span class="avail-mono" id="failedHaltedAt">halted at stage 1</span>
              </div>
              <ol class="spine-lg" id="spineFailed"></ol>
            </div>
            <div class="card stack-2" data-od-id="failed-survived">
              <h2 class="card-title">What survived the halt</h2>
              <p class="eyebrow" style="margin:0">counted from the event stream</p>
              <dl class="kv" id="failedCounters"></dl>
              <p class="avail">The same counters the running stage keeps, frozen at the halt. A counter whose node never ran reads <span class="mono">not reached</span>: a value never measured, not a zero.</p>
            </div>
          </aside>
        </div>
```

- [ ] **Step 3: Rewrite `populateFailed` (from `function populateFailed(session){` through its closing `}`)**

```js
  /* Halting types (graph/state.py:141-150) in plain words; the enumerated type stays in the facts. */
  var HALT_HEADLINES = {
    graph_planning_failed:                 "Planning failed",
    graph_provider_configuration_error:    "Model provider misconfigured",
    graph_agent_configuration_error:       "Agent misconfigured",
    graph_invalid_agent_state:             "Invalid agent state",
    graph_invalid_route:                   "Invalid route",
    graph_request_attempt_limit_exceeded:  "Request attempt limit reached"
  };
  function populateFailed(session){
    var hard = (session.errors || []).filter(function(x){ return !x.recoverable; })[0]
            || { type:"api.research.failed", source:"api" };
    setText("failedMeta", "session " + session.id + " · finished " + (fmtClock(session.finished) || "not recorded"));
    setText("failed-h", session.q);
    setText("failedType", HALT_HEADLINES[hard.type] || hard.type);
    /* `ResearchError.message` is curated per enumerated type and never str(exception)
       (graph/errors.py); the static sentence is only the fallback for a record without one. */
    setText("failedMessage", hard.message || "The run stopped on a non-recoverable error.");
    setText("failFactType", hard.type);
    setText("failFactSource", hard.source || "graph");
    setText("failFactRecoverable", String(!!hard.recoverable));
    var trace = document.getElementById("failedTrace");
    if(trace){
      if(session.trace){ trace.style.display = ""; trace.href = session.trace; }
      else trace.style.display = "none";
    }
    /* The halted run's events go through the same handlers as a live run. The halting row is
       the last graph.node.started with no graph.node.completed; every graph.node.skipped row is
       `skipped`; Publishing is `skipped` by the client rule status == "failed" — never
       has_report, which can be true after a first-pass writer (report_writer.py:3262) — because
       the graph never runs finalize_report after a halt and no event exists for it (spec §3.1). */
    var run = newRunState(session.passes);
    HALTED_EVENTS.forEach(function(ev){ applyEvent(run, ev); });
    var marks = marksFor(run, run.openNode);
    if(run.finalStatus === "failed") marks.finalize_report = "skipped";
    renderSpine(document.getElementById("spineFailed"), marks, run);
    var haltIndex = AGENT_ORDER.indexOf(run.openNode);
    setText("failedHaltedAt", haltIndex >= 0 ? "halted at stage " + (haltIndex + 1) : "halted before the first stage");
    renderCounters("failedCounters", run.counters, "not reached");
  }
```

- [ ] **Step 4: Run the check**

Run: `node "$TEMP/dr-probe/probe.mjs" docs/design/prototype index.html "$TEMP/dr-probe/t6-failed.js"`
Expected: exit 0, `"ok": true`.

Run: `rg -n -e 'claims checked' -e 'sources evaluated' -e 'Why there is no report' docs/design/prototype/index.html`
Expected: prints nothing.

- [ ] **Step 5: Commit**

```bash
git add docs/design/prototype/index.html
git commit -m "design(prototype): failed stage names the halting type in plain words and derives skipped rows and survived counters from the halted events"
```

---

### Task 7: Report stage — consumer-format body, findings table, rail cards (spec §4.4 report side and rail, AC11, AC12 index.html parts, AC13, AC6 finished half)

**Files:**
- Modify: `docs/design/prototype/index.html:1229-1334` (`<!-- ── STAGE · report` section through the rail's closing `</aside>`), `:1033` (append CSS), `function populateReport(session){` (today `:2833-2907`), `:1521` (next to `fmtDur`), boot's `paintMeters();` call (today `:3026`)
- Test: `$TEMP/dr-probe/t7-report.js`, `$TEMP/dr-probe/t7-widths.js`, `$TEMP/dr-probe/t7-finish.js`, `$TEMP/dr-probe/t7-null.js`

**Interfaces:**
- Consumes: `statusNote`, `passText`, `fmtScore`, `SESSIONS` fields `review/coverage/evidenceCounts/durationSeconds/evidencePath/qualityPath` (Task 1); `finishPlayback` (Task 4).
- Produces: report card markup ids `#repEvidenceLine`, `#repFindingsFrame`, `#repSources`, `#repEvidenceLink`; rail ids `#repReviewRow/#repReviewFill/#repReviewN/#repCitedFill/#repCitedN/#repReviewStatus`, `#repCovAnswered/#repCovNotFound`, `#repEvidenceCounts/#repEvidenceNone`, `#repFactPass/#repFactDuration/#repFactEvidence/#repFactQuality`; functions `fmtSeconds(s)`, `ratio(a, b) → number|null`, `setMeterN(id, textOrNull, absentText)`, `setMeterFill(id, v)`, `renderEvidenceCounts(ec)`; `populateReport(session)` calls `paintMeters(document.getElementById("stage-report"))`. Task 8 inserts the toggle into `.report-head-bar` and a sibling view after `.with-rail.report-main`; it relies on the report body being wrapped in `<div class="with-rail report-main">`.

- [ ] **Step 1: Write the failing report check (AC11, AC13)**

```bash
cat > "$TEMP/dr-probe/t7-report.js" <<'EOF'
c.open("b41e77aa"); await waitFor(() => c.stage() === "report", 3000);
const card = $('[data-od-id="report-body"]');
const kids = Array.from(card.querySelector('.prose').children).map((el) => el.tagName.toLowerCase() + (el.id ? '#' + el.id : '') + (el.className ? '.' + el.className.split(' ')[0] : ''));
const h2s = $$('[data-od-id="report-body"] h2').map((h) => h.textContent.trim());
const markers = $$('[data-od-id="report-body"] .prose ul a.cite');
const anchorsOk = markers.length > 0 && markers.every((a) => /^#src-\d+$/.test(a.getAttribute('href')) && /^\[\d+\]$/.test(a.textContent.trim()));
const sources = $$('#repSources li');
const sourcesOk = sources.length === 6 && sources.every((li, i) => li.id === 'src-' + (i + 1) && li.querySelector('a').target === '_blank' && /noopener/.test(li.querySelector('a').rel));
const caption = card.querySelector('.tbl-foot em');
const rail = $$('[data-od-id="report-rail"] .card-title').map((h) => h.textContent.trim());
const review = { fill: $('#repReviewFill').className, n: text('#repReviewN'), status: text('#repReviewStatus'), cited: text('#repCitedN'), gate: getComputedStyle($('#repReviewRow .bar'), '::after').width };
const evidence = $$('#repEvidenceCounts dd').map((d) => d.textContent.trim());
const facts = Object.fromEntries($$('[data-od-id="report-facts"] dt').map((dt) => [dt.textContent.trim(), dt.nextElementSibling.textContent.trim()]));
const forbidden = /Executive Summary|Verified claims|Limitations|Citations|claim confidence|source authority|corroboration/i.test($('#stage-report').textContent);
c.open("9ea4c220"); await waitFor(() => c.stage() === "report", 3000);
const yellow = $('#repCitedFill').classList.contains('warn') && text('#repCitedN') === '0.80' && text('#repReviewN') === 'not scored' && !$('#repReviewFill').classList.contains('ok');
const ok = kids[0] === 'p#repEvidenceLine.avail' && h2s[0] === 'Bottom line' && !!card.querySelector('#repFindingsFrame table.tbl') && !!caption
  && h2s.includes("What we couldn't confirm") && h2s[h2s.length - 1] === 'Sources' && anchorsOk && sourcesOk
  && text('#repEvidenceLink') === 'How this was researched: evidence log'
  && JSON.stringify(rail) === JSON.stringify(['Review','Coverage','Evidence','Session facts','Cost and usage','Errors'])
  && review.fill.includes('ok') && review.n === '0.86' && review.status === 'review accepted · 0.86 · 1 target not found' && review.cited === '0.86' && review.gate === '1px'
  && evidence.length === 10 && evidence[7] === '24 network · 7 cache'
  && facts['pass'] === 'pass 2 of 2' && facts['duration_seconds'] === '552' && facts['evidence_path'] === 'api-output/report-evidence.md' && facts['quality_path'] === 'api-output/report-quality.json'
  && text('#repCovAnswered') === '3 of 4' && text('#repCovNotFound') === 'T04' && !forbidden && yellow
  && /pass 2 of 2$/.test(text('#reportMeta'));
return { ok, kids, h2s, anchorsOk, sourcesOk, rail, review, evidence, facts, forbidden, yellow, meta: text('#reportMeta') };
EOF
node "$TEMP/dr-probe/probe.mjs" docs/design/prototype index.html "$TEMP/dr-probe/t7-report.js"
```

Expected: exit 1 (`h2s[0]` is `Executive Summary`; `#repReviewFill` is null → `error`).

- [ ] **Step 2: Write the failing width check (AC12, index.html parts) — at phone width the findings frame may overflow, so `fits` is required only above 900 px**

```bash
cat > "$TEMP/dr-probe/t7-widths.js" <<'EOF'
c.open("b41e77aa"); await waitFor(() => c.stage() === "report", 3000);
const measure = () => { const f = $('#repFindingsFrame'), t = f.querySelector('table'); return { frame: f.clientWidth, table: t.scrollWidth, fits: t.scrollWidth <= f.clientWidth, sticky: getComputedStyle(t.querySelector('td')).position, page: document.scrollingElement.scrollWidth <= innerWidth, overflow: getComputedStyle(f).overflowX }; };
const expanded = measure();
if (innerWidth > 1080) { $('#sidebarToggle').click(); await sleep(300); }
const collapsed = measure();
const prose = $('[data-od-id="report-body"] .prose').getBoundingClientRect().width;
const desktop = innerWidth > 900;
const ok = expanded.page && collapsed.page && expanded.overflow === 'auto' && expanded.sticky === 'static'
  && (!desktop || (expanded.fits && collapsed.fits && expanded.frame === 534 && prose <= 720));
return { ok, expanded, collapsed, prose, innerWidth, desktop };
EOF
node "$TEMP/dr-probe/probe.mjs" docs/design/prototype index.html "$TEMP/dr-probe/t7-widths.js"
node "$TEMP/dr-probe/probe.mjs" docs/design/prototype index.html "$TEMP/dr-probe/t7-widths.js" --viewport=390x844
```

Expected: both exit 1 (`#repFindingsFrame` is null → `error`).

- [ ] **Step 3: Rewrite the report stage body (`:1244-1334`, from `<div class="with-rail">` inside `#stage-report` through the `</aside>` that closes the rail)**

```html
        <div class="with-rail report-main">
          <div class="stack" style="gap:var(--space-6)">
            <!-- The server's Markdown, rendered as-is: the server owns wording, order, numbering
                 and the table (spec 2026-09-25 §3). Presentation-only adaptations (spec §4.4): the
                 title is not repeated, [n] markers become anchors, the sources list carries ids,
                 the table sits in a horizontally scrolling frame, the evidence-log link is muted text
                 until the service serves the log (api-gaps E1). One battery-storage report is reused
                 for every finished session (DESIGN.md §7). -->
            <article class="card stack" style="gap:var(--space-5)" data-od-id="report-body">
              <div class="prose">
                <p class="avail" id="repEvidenceLine">Evidence as of 2026-09-16 · 6 sources</p>
                <h2 style="margin-top:var(--space-4)">Bottom line</h2>
                <p>Grid connection is the binding constraint on grid-scale battery deployment in the evidence this run could verify, and that evidence is almost entirely from the United States: 2,600 GW of proposed generation and storage were waiting for grid access at the end of 2023, with typical waits of five years or more <a class="cite" href="#src-6">[6]</a>. The supply side is concentrated in one country: China processed 70 to 95 percent of global lithium, cobalt, phosphate and graphite in 2024 <a class="cite" href="#src-2">[2]</a>. Queue reform under FERC Order 2023 has begun and U.S. queue capacity fell for the first time in 2024, but nothing verified shows the bottleneck resolved <a class="cite" href="#src-6">[6]</a>.</p>

                <div class="tbl-frame" id="repFindingsFrame">
                  <table class="tbl tbl-findings">
                    <thead><tr><th>What was measured</th><th>Result</th><th>Who reported it (and when)</th><th class="n">Source</th></tr></thead>
                    <tbody>
                      <tr><td class="wrapcell">Proposed U.S. generation and storage awaiting grid access</td><td class="wrapcell">2,600 GW</td><td class="wrapcell">Lawrence Berkeley National Laboratory (end of 2023)</td><td class="n"><a class="cite" href="#src-6">[6]</a></td></tr>
                      <tr><td class="wrapcell">Typical U.S. interconnection wait</td><td class="wrapcell">5 years or more</td><td class="wrapcell">Lawrence Berkeley National Laboratory (2024)</td><td class="n"><a class="cite" href="#src-6">[6]</a></td></tr>
                      <tr><td class="wrapcell">Share of queued U.S. projects expected to reach completion</td><td class="wrapcell">under 25%</td><td class="wrapcell">Lawrence Berkeley National Laboratory (2024)</td><td class="n"><a class="cite" href="#src-6">[6]</a></td></tr>
                      <tr><td class="wrapcell">China's share of global lithium, cobalt, phosphate and graphite processing</td><td class="wrapcell">70–95%</td><td class="wrapcell">IEA, Global Critical Minerals Outlook (2025, for 2024)</td><td class="n"><a class="cite" href="#src-2">[2]</a></td></tr>
                      <tr><td class="wrapcell">Annual production at risk under full Chinese export controls</td><td class="wrapcell">$6.5 trillion</td><td class="wrapcell">IEA, as reported by Anadolu Agency (2025)</td><td class="n"><a class="cite" href="#src-3">[3]</a></td></tr>
                      <tr><td class="wrapcell">China's average share of mineral supply, excluding rare earths</td><td class="wrapcell">72% in 2025, from 70% in 2023</td><td class="wrapcell">Congressional Research Service, R48149 (2025)</td><td class="n"><a class="cite" href="#src-4">[4]</a></td></tr>
                      <tr><td class="wrapcell">Consumer loss per $1 billion of delayed transmission investment</td><td class="wrapcell">$150–370 million per year of delay</td><td class="wrapcell">Enki AI (2026)</td><td class="n"><a class="cite" href="#src-5">[5]</a></td></tr>
                    </tbody>
                  </table>
                </div>
                <p class="tbl-foot"><em>Figures verified against their source pages; the source column links to the numbered list below.</em></p>

                <h2>Grid connection</h2>
                <ul>
                  <li>As of the end of 2023, 2,600 GW of proposed U.S. generation and storage projects were waiting for grid access, with typical wait times stretching to five years or more <a class="cite" href="#src-6">[6]</a>.</li>
                  <li>At current rates, less than a quarter of the energy projects entering U.S. interconnection queues are expected to reach completion <a class="cite" href="#src-6">[6]</a>.</li>
                  <li>Aggregated reporting across U.S. ISOs and RTOs places over 2,000 GW in queues, with delays of three to seven years in some markets; Google has reported potential transmission connection delays of up to 12 years for new data centres <a class="cite" href="#src-5">[5]</a>.</li>
                  <li>For every $1 billion of delayed transmission investment, consumers lose between $150 million and $370 million in net benefits per year of delay <a class="cite" href="#src-5">[5]</a>.</li>
                </ul>

                <h2>Critical minerals and supply-chain concentration</h2>
                <ul>
                  <li>The IEA reports that China retained its dominance over the midstream and downstream EV and storage battery supply chain in 2024, processing 70 to 95 percent of global lithium, cobalt, phosphate and graphite <a class="cite" href="#src-2">[2]</a>.</li>
                  <li>Excluding rare earths, China's average share of mineral supply rose to 72 percent in 2025 from 70 percent in 2023, and the number of mineral tariff categories covered by its export controls has tripled since 2023 <a class="cite" href="#src-4">[4]</a>.</li>
                  <li>The IEA estimates that full implementation of China's export controls could put $6.5 trillion in annual production outside China at risk <a class="cite" href="#src-3">[3]</a>.</li>
                  <li>Resource nationalism is rising among producer countries, which narrows the routes around any one supplier <a class="cite" href="#src-1">[1]</a>.</li>
                </ul>

                <h2>Queue reform</h2>
                <ul>
                  <li>FERC's Order 2023 mandated interconnection queue reform, including stronger financial and site-control requirements to discourage speculative projects and improved storage modelling; Order 2023-A reaffirmed most of it <a class="cite" href="#src-6">[6]</a>.</li>
                  <li>U.S. queue capacity fell for the first time in 2024, but the verified evidence does not show the bottleneck resolved <a class="cite" href="#src-6">[6]</a>.</li>
                </ul>

                <h2>What we couldn't confirm</h2>
                <p>Non-U.S. grid connection regimes were sought, but no page read gave a verified figure. Tariffs and trade barriers, siting and fire-code compliance, wholesale market accreditation rules, equipment lead times and financing conditions were raised in review and have no verified finding in this run.</p>

                <h2>Sources</h2>
                <ol class="sources" id="repSources">
                  <li id="src-1">Resources for the Future — <a class="tlink" href="https://www.rff.org/" target="_blank" rel="noopener">Resource Nationalism and the Resilience of Critical Mineral Supply Chains</a> (2025)</li>
                  <li id="src-2">IEA — <a class="tlink" href="https://www.iea.org/" target="_blank" rel="noopener">Global Critical Minerals Outlook 2025</a> (2025)</li>
                  <li id="src-3">Anadolu Agency — <a class="tlink" href="https://www.aa.com.tr/" target="_blank" rel="noopener">Why the IEA says critical minerals have become a $6.5 trillion economic security risk</a> (2025)</li>
                  <li id="src-4">Congressional Research Service — <a class="tlink" href="https://crsreports.congress.gov/" target="_blank" rel="noopener">Critical Minerals and Materials for Selected Energy Technologies</a> (2025)</li>
                  <li id="src-5">Enki AI — <a class="tlink" href="https://www.enkiai.com/" target="_blank" rel="noopener">Grid Interconnection Delays 2026: A Threat to US Energy</a> (2026)</li>
                  <li id="src-6">Novogradac — <a class="tlink" href="https://www.novoco.com/" target="_blank" rel="noopener">Resolving the Interconnection Queue Bottleneck</a> (2024)</li>
                </ol>
                <!-- The report's last line. The relative link to the evidence log is rendered as muted text
                     with the same words until the service serves the log's Markdown (api-gaps E1); with E1 it
                     switches the toggle to Evidence instead. -->
                <p class="avail" id="repEvidenceLink">How this was researched: <span class="mono">evidence log</span></p>
              </div>
            </article>
          </div>

          <aside class="rail" data-od-id="report-rail" aria-label="Report details">
            <div class="card stack-2" data-od-id="report-review">
              <h2 class="card-title">Review</h2>
              <div class="bars">
                <div class="bar-row" id="repReviewRow"><span style="color:var(--fg)">Review score</span><span class="bar bar-gate"><span id="repReviewFill"></span></span><span class="n" id="repReviewN">—</span></div>
                <div class="bar-row"><span style="color:var(--fg)">Scored sources cited</span><span class="bar"><span id="repCitedFill"></span></span><span class="n" id="repCitedN">—</span></div>
              </div>
              <p class="avail" id="repReviewStatus">—</p>
            </div>

            <div class="card stack-2" data-od-id="report-coverage">
              <h2 class="card-title">Coverage</h2>
              <dl class="kv">
                <dt>required targets answered</dt><dd id="repCovAnswered">—</dd>
                <dt>not found</dt><dd id="repCovNotFound">—</dd>
              </dl>
            </div>

            <div class="card stack-2" data-od-id="report-evidence">
              <h2 class="card-title">Evidence</h2>
              <dl class="kv" id="repEvidenceCounts"></dl>
              <p class="avail" id="repEvidenceNone" hidden>not measured</p>
            </div>

            <div class="card stack-2" data-od-id="report-facts">
              <h2 class="card-title">Session facts</h2>
              <dl class="kv">
                <dt>status</dt><dd id="repFactStatus">—</dd>
                <dt>pass</dt><dd id="repFactPass">—</dd>
                <dt>started_at</dt><dd id="repFactStarted">—</dd>
                <dt>finished_at</dt><dd id="repFactFinished">—</dd>
                <dt>duration_seconds</dt><dd id="repFactDuration">—</dd>
                <dt>report_path</dt><dd id="repFactPath">—</dd>
                <dt>evidence_path</dt><dd id="repFactEvidence">—</dd>
                <dt>quality_path</dt><dd id="repFactQuality">—</dd>
                <dt>errors</dt><dd id="repFactErrors">—</dd>
              </dl>
            </div>

            <div class="card stack-2" data-od-id="report-cost">
              <h2 class="card-title">Cost and usage</h2>
              <dl class="kv">
                <dt>tool calls</dt><dd class="avail">Not recorded</dd>
                <dt>input tokens</dt><dd class="avail">Not recorded</dd>
                <dt>output tokens</dt><dd class="avail">Not recorded</dd>
              </dl>
              <p class="avail">The response carries no token or tool-call totals. The running stage's tool-call counter is a stream-derived, researcher-only figure and is not copied here. Nothing is rendered as <span class="mono">0</span>.</p>
            </div>

            <div class="card stack-2" data-od-id="report-errors">
              <h2 class="card-title">Errors</h2>
              <details class="disc">
                <summary><span id="repErrSummary">—</span></summary>
                <div class="disc-body stack-2" id="repErrList"></div>
              </details>
            </div>
          </aside>
        </div>
```

- [ ] **Step 4: Append CSS before `</style>`**

```css
/* ═══ 2026-09-26: the consumer-format report ═══ */
/* Tables sit in a frame that scrolls sideways; the page itself never does. The findings table
   (4 columns) fits the desktop column; the options table (up to 6 columns, states.html) pins
   its Option column over the scroll. */
.tbl-frame{overflow-x:auto;max-width:100%;-webkit-overflow-scrolling:touch}
.tbl-frame .tbl{margin:0}
.tbl-frame .tbl td .cite{color:var(--fg)}
.prose .sources{margin:0 0 var(--space-4);padding-left:var(--space-5)}
.prose .sources li{margin-bottom:var(--space-2);overflow-wrap:anywhere}
/* The 0.80 acceptance line on the review meter (types.py:1131). */
.bar-row .bar{position:relative}
.bar-row .bar.bar-gate::after{content:"";position:absolute;left:80%;top:0;bottom:0;width:1px;background:var(--fg);opacity:.75}
```

- [ ] **Step 5: Rewrite `populateReport` (from `/* ═══ report + failed population ═══ */` through the closing `}` of `populateReport`; `populateFailed` below it stays as Task 6 wrote it)**

```js
  /* ═══ report population ═══ */
  function ratio(a, b){
    return (typeof a === "number" && typeof b === "number" && isFinite(a) && b > 0) ? a / b : null;
  }
  /* A meter's figure: two decimals, or muted absence text. paintMeters() reads the text back,
     so an absent figure keeps the default grey fill (DESIGN.md §3.6). */
  function setMeterN(id, value, absentText){
    var el = document.getElementById(id);
    if(!el) return;
    el.textContent = value === null ? absentText : value;
    el.classList.toggle("avail", value === null);
  }
  function setMeterFill(id, v){
    var el = document.getElementById(id);
    if(el) el.style.width = v === null ? "0%" : Math.round(Math.max(0, Math.min(1, v)) * 100) + "%";
  }
  function renderEvidenceCounts(ec){
    var host = document.getElementById("repEvidenceCounts");
    var none = document.getElementById("repEvidenceNone");
    if(!host || !none) return;
    host.textContent = "";
    if(!ec){ host.hidden = true; none.hidden = false; return; }
    host.hidden = false; none.hidden = true;
    /* eleven values in ten rows (spec §4.4 rail card 3) */
    [["findings", ec.findings], ["verified", ec.verified_findings], ["corrected", ec.corrected_findings],
     ["quoted", ec.quoted_findings], ["dropped", ec.dropped_findings], ["context unchecked", ec.context_unchecked_findings],
     ["cited", ec.cited_findings], ["reads", ec.network_reads + " network · " + ec.cache_reads + " cache"],
     ["unique works", ec.unique_works], ["publishers", ec.publishers]].forEach(function(row){
      var dt = document.createElement("dt"); dt.textContent = row[0];
      var dd = document.createElement("dd"); dd.textContent = String(row[1]);
      host.appendChild(dt); host.appendChild(dd);
    });
  }
  function populateReport(session){
    var dur = fmtSeconds(session.durationSeconds);
    setText("reportMeta", "session " + session.id + " · finished " + (fmtClock(session.finished) || "not recorded")
      + (dur ? " · " + dur : "") + " · " + passText(session));
    setText("report-h", session.q);
    /* Review card: the meter, the 0.80 line (CSS), the status text = the chip's second clause */
    var score = (session.review && typeof session.review.score === "number") ? session.review.score : null;
    setMeterFill("repReviewFill", score);
    setMeterN("repReviewN", fmtScore(score), "not scored");
    setText("repReviewStatus", statusNote(session));
    var ec = session.evidenceCounts || null;
    var cited = ec ? ratio(ec.cited_assessed_sources, ec.assessed_sources) : null;
    setMeterFill("repCitedFill", cited);
    setMeterN("repCitedN", cited === null ? null : cited.toFixed(2), "not measured");
    paintMeters(document.getElementById("stage-report"));
    /* Coverage card */
    var cov = session.coverage || null;
    var covAnswered = document.getElementById("repCovAnswered");
    if(covAnswered){
      covAnswered.textContent = cov ? cov.answered_targets + " of " + cov.required_targets : "not measured";
      covAnswered.classList.toggle("avail", !cov);
    }
    var covNotFound = document.getElementById("repCovNotFound");
    if(covNotFound){
      var ids = (cov && cov.not_found_target_ids) || [];
      covNotFound.textContent = ids.length ? ids.join(" · ") : "none";
      covNotFound.classList.toggle("avail", !ids.length);
    }
    /* Evidence card */
    renderEvidenceCounts(ec);
    /* Session facts */
    setText("repFactStatus", session.status);
    setText("repFactPass", passText(session));
    setText("repFactStarted", session.started);
    setText("repFactFinished", session.finished || "not recorded");
    var durEl = document.getElementById("repFactDuration");
    if(durEl){
      var hasDur = typeof session.durationSeconds === "number";
      durEl.textContent = hasDur ? String(session.durationSeconds) : "not recorded";
      durEl.className = hasDur ? "" : "avail";
    }
    [["repFactPath", session.reportPath], ["repFactEvidence", session.evidencePath], ["repFactQuality", session.qualityPath]].forEach(function(p){
      var el = document.getElementById(p[0]);
      if(!el) return;
      if(p[1]){ el.textContent = p[1]; el.className = ""; }
      else { el.textContent = "Not published"; el.className = "avail"; }
    });
    setText("repFactErrors", String((session.errors || []).length));
    var dl = document.getElementById("downloadBtn");
    if(dl) dl.style.display = session.reportPath ? "" : "none";
    var trace = document.getElementById("traceBtn");
    if(trace){
      if(session.trace){ trace.style.display = ""; trace.href = session.trace; }
      else trace.style.display = "none";
    }
    /* Errors: grouped list with the API's own message beside the enumerated type */
    var errs = session.errors || [];
    var rec = errs.filter(function(x){ return x.recoverable; }).length;
    setText("repErrSummary", rec + " recoverable · " + (errs.length - rec) + " non-recoverable");
    var rl = document.getElementById("repErrList");
    if(rl){
      rl.textContent = "";
      if(!errs.length){
        var none = document.createElement("span");
        none.className = "avail";
        none.textContent = "No errors recorded.";
        rl.appendChild(none);
      } else {
        errs.forEach(function(x){
          var row = document.createElement("div");
          var t = document.createElement("span");
          t.className = "avail-mono";
          t.textContent = x.type;
          row.appendChild(t);
          if(x.message){
            var m = document.createElement("span");
            m.className = "sm";
            m.style.display = "block";
            m.style.marginTop = "var(--space-1)";
            m.textContent = x.message;
            row.appendChild(m);
          }
          rl.appendChild(row);
        });
      }
    }
  }
```

Add next to `fmtDur` (`:1521`) — keep `fmtDur` for other callers:

```js
  function fmtSeconds(s){
    if(typeof s !== "number" || !isFinite(s) || s < 0) return null;
    return Math.floor(s / 60) + "m " + String(Math.round(s % 60)).padStart(2, "0") + "s";
  }
```

In `boot()` delete the `paintMeters();` call and its comment (today `:3023-3026`): the meters are now session data painted by `populateReport()`.

- [ ] **Step 6: Run the report and width checks**

```bash
node "$TEMP/dr-probe/probe.mjs" docs/design/prototype index.html "$TEMP/dr-probe/t7-report.js"
node "$TEMP/dr-probe/probe.mjs" docs/design/prototype index.html "$TEMP/dr-probe/t7-widths.js"
node "$TEMP/dr-probe/probe.mjs" docs/design/prototype index.html "$TEMP/dr-probe/t7-widths.js" --viewport=390x844
```

Expected: all exit 0. At 1252 the expanded frame is 534 px (`1252 − 296 − 48 − 300 − 32 − 42`) and `prose <= 720` when collapsed; at 390 `page` is true and the frame's `overflow-x` is `auto`.

- [ ] **Step 7: AC6 finished half — the composer run at budget 0 ends on the report stage**

```bash
cat > "$TEMP/dr-probe/t7-finish.js" <<'EOF'
$('#plusBtn').click(); await sleep(50); $('#extraMinus').click(); $('#popClose').click();
c.submit("What is the current state of sodium-ion battery energy density?");
await waitFor(() => c.stage() === "running", 6000);
c.advanceTo("graph.session.completed"); c.finish();
await waitFor(() => c.stage() === "report", 3000);
const ev = $$('#repEvidenceCounts dd').map((d) => d.textContent.trim());
return { ok: text('#topbarStatus') === 'Completed · review accepted · 0.86' && text('#repCovAnswered') === '4 of 4' && text('#repCovNotFound') === 'none'
             && ev.length === 10 && $('#repEvidenceNone').hidden === true && /pass 1 of 1$/.test(text('#reportMeta')) && text('#repFactPass') === 'pass 1 of 1',
         chip: text('#topbarStatus'), cov: [text('#repCovAnswered'), text('#repCovNotFound')], ev, meta: text('#reportMeta') };
EOF
node "$TEMP/dr-probe/probe.mjs" docs/design/prototype index.html "$TEMP/dr-probe/t7-finish.js"
```

Expected: exit 0, `"ok": true`.

- [ ] **Step 8: Scoped stale-name check**

Run: `rg -n -e 'Executive Summary' -e 'Verified claims' -e 'repLimitations' -e 'citations-table' -e '[Cc]laim confidence' -e '[Cc]orroboration' -e 'Quality Snapshot' docs/design/prototype/index.html`
Expected: prints nothing.

- [ ] **Step 9: Review Focus 3 — `evidence_counts: null` and `assessed_sources: 0`**

```bash
cat > "$TEMP/dr-probe/t7-null.js" <<'EOF'
const s = c.sessions.find((x) => x.id === "5ff1ab07");
s.evidenceCounts = null;
c.open("5ff1ab07"); await waitFor(() => c.stage() === "report", 3000);
const nul = { none: $('#repEvidenceNone').hidden, rows: $$('#repEvidenceCounts dd').length, cited: text('#repCitedN'), citedCls: $('#repCitedN').className, fill: $('#repCitedFill').className };
s.evidenceCounts = Object.assign({}, c.sessions.find((x) => x.id === "b41e77aa").evidenceCounts, { assessed_sources: 0, cited_assessed_sources: 0 });
c.open("b41e77aa"); await waitFor(() => c.stage() === "report", 3000);
c.open("5ff1ab07"); await waitFor(() => c.stage() === "report", 3000);
const zero = { cited: text('#repCitedN'), rows: $$('#repEvidenceCounts dd').length, nan: /NaN/.test($('#stage-report').textContent) };
return { ok: nul.none === false && nul.rows === 0 && nul.cited === 'not measured' && /avail/.test(nul.citedCls) && !/ok|warn|danger/.test(nul.fill)
             && zero.cited === 'not measured' && zero.rows === 10 && !zero.nan, nul, zero };
EOF
node "$TEMP/dr-probe/probe.mjs" docs/design/prototype index.html "$TEMP/dr-probe/t7-null.js"
```

Expected: exit 0, `"ok": true`.

- [ ] **Step 10: Commit**

```bash
git add docs/design/prototype/index.html
git commit -m "design(prototype): report stage renders the consumer format with a findings table; rail shows review, coverage, evidence counts and session facts"
```

---

### Task 8: The Evidence view — `Report | Evidence` toggle, filter chips, list, detail pane, `drConsole.view/evidence` (spec §4.4 Evidence side, Q2, Q4, AC14, AC15 hook half)

**Files:**
- Modify: `docs/design/prototype/index.html:1233-1240` (`.report-head-bar`: insert the toggle between `#reportMeta` and the actions), after the `</div>` closing `.with-rail.report-main` from Task 7 (insert the Evidence view), `:1033` (append CSS), after `populateReport` (add the Evidence functions), `drConsole` (add `view`, `evidence`)
- Test: `$TEMP/dr-probe/t8-evidence.js`, `$TEMP/dr-probe/t8-keys.js`

**Interfaces:**
- Consumes: `paintMeters(root)`, `fmtScore`, `openSession`, `activeSession`, `populateReport` (this task appends a `setView("report")` call to it).
- Produces: `EVIDENCE.default` (E1 shape: `{session_id, iteration, findings[], not_found[], refused[]}`), `evidenceFor(session)`, `EV_FILTERS`, `evidenceRows(data) → rows[{kind, status, id, item}]`, `setView(name)`, `currentView()`, `renderEvidence(session)`, `evSelect(index)`, `drConsole.view(name?)`, `drConsole.evidence(sessionId)`; `#stage-report[data-view]`; ids `#segView`, `#evidenceView`, `#evChips`, `#evList`, `#evDetail`. Task 10's `08-evidence` capture calls `view("evidence")`.

- [ ] **Step 1: Write the failing check (AC14, AC15 hook half)**

```bash
cat > "$TEMP/dr-probe/t8-evidence.js" <<'EOF'
c.open("b41e77aa"); await waitFor(() => c.stage() === "report", 3000);
const v0 = c.view();
c.view("evidence");
const v1 = [c.view(), $('#stage-report').dataset.view, $$('#segView button').map((b) => b.getAttribute('aria-pressed')).join(',')];
const chips = $$('#evChips button').map((b) => b.textContent.replace(/\s+/g, ' ').trim());
const rowsUnder = (label) => { $$('#evChips button').find((b) => b.textContent.trim().startsWith(label)).click(); return $$('#evList li').map((li) => li.dataset.status); };
const all = rowsUnder('All');
const per = {}; for (const f of ['Verified','Corrected','Quoted','Dropped','Not found','Refused']) per[f] = rowsUnder(f);
const statusOf = { Verified:'verified', Corrected:'verified_corrected', Quoted:'quoted', Dropped:'dropped', 'Not found':'not_found', Refused:'refused' };
const counts = Object.fromEntries(chips.map((t) => { const m = /^(.*?) (\d+)$/.exec(t); return [m[1], +m[2]]; }));
const filtersOk = Object.keys(statusOf).every((f) => per[f].length === counts[f] && per[f].every((s) => s === statusOf[f]));
rowsUnder('All');
$$('#evList li').find((li) => li.dataset.id === 'F03').click();
const finding = { title: text('#evDetail .ev-title'), status: text('#evDetail .ev-status'), meters: $$('#evDetail .bar-row .n').map((n) => n.textContent.trim()),
  overallCls: $$('#evDetail .bar-row .bar span')[3].className, hasSnippet: /Snippet/.test($('#evDetail').textContent), context: $$('#evDetail p.sm').map((p) => p.textContent).find((t) => t.startsWith('Context check')),
  source: $('#evDetail a.tlink') && $('#evDetail a.tlink').target };
$$('#evList li').find((li) => li.dataset.id === 'F01').click();
const f01 = { context: $$('#evDetail p.sm').map((p) => p.textContent).find((t) => t.startsWith('Context check')), figures: $$('#evDetail .ev-figures li').map((li) => li.textContent) };
$$('#evList li').find((li) => li.dataset.id === 'F06').click();
const f06 = { status: text('#evDetail .ev-status'), context: $$('#evDetail p.sm').map((p) => p.textContent).find((t) => t.startsWith('Context check')), tag: !!$$('#evList li[data-id="F06"] .tag').find((t) => t.textContent === 'context unchecked') };
$$('#evList li').find((li) => li.dataset.kind === 'not_found').click();
const nf = { title: text('#evDetail .ev-title'), queries: $$('#evDetail .ev-queries li').length, pages: $$('#evDetail .ev-pages a').length };
$$('#evList li').find((li) => li.dataset.kind === 'refused').click();
const rf = { title: text('#evDetail .ev-title'), cited: $$('#evDetail .ev-cited button').map((b) => b.textContent.trim()), hasReason: /reason/i.test($('#evDetail').textContent), hasWhere: /where/i.test($('#evDetail').textContent) };
c.view("report");
const back = [c.view(), $('#stage-report').dataset.view];
c.evidence("7c0d13ff"); await waitFor(() => c.stage() === "report", 3000);
const viaHook = [c.stage(), c.view()];
const ok = v0 === 'report' && v1[0] === 'evidence' && v1[1] === 'evidence' && v1[2] === 'false,true'
  && chips.length === 7 && chips[0].startsWith('All') && all.length === counts.All && all.includes('not_checked') && all.length === 8
  && filtersOk && Object.values(per).flat().every((s) => s !== 'not_checked')
  && finding.title.startsWith('F03') && finding.status === 'quoted (snippet found on the page; not checked for context)' && finding.meters.length === 4 && finding.meters[3] === '0.80'
  && finding.overallCls === 'warn' && finding.hasSnippet && finding.context === 'Context check: not run (no figures)' && finding.source === '_blank'
  && f01.context === 'Context check: Lawrence Berkeley National Laboratory · actual · 2024 queue study' && f01.figures.length === 1 && /^2,600 GW: kept · period end of 2023/.test(f01.figures[0])
  && /; context unchecked$/.test(f06.status) && f06.context === 'Context check: context unchecked' && f06.tag
  && nf.title.startsWith('T04') && nf.queries === 2 && nf.pages === 2
  && rf.cited.includes('F03') && rf.hasReason && rf.hasWhere
  && back[0] === 'report' && back[1] === 'report' && viaHook[0] === 'report' && viaHook[1] === 'evidence';
return { ok, v0, v1, chips, all, per, counts, finding, f01, f06, nf, rf, back, viaHook };
EOF
node "$TEMP/dr-probe/probe.mjs" docs/design/prototype index.html "$TEMP/dr-probe/t8-evidence.js"
```

Expected: exit 1 (`c.view is not a function`).

- [ ] **Step 2: Insert the toggle into `.report-head-bar` (`:1234-1239`)** — between `<p class="cap" id="reportMeta" …>` and `<div class="row wrap">`:

```html
            <!-- Report | Evidence (Q2): two views over one session; Report is the default, the
                 choice is per session and not persisted. Same recipe as the composer's segments. -->
            <div class="seg seg-view" role="group" aria-label="Report view" id="segView">
              <button type="button" data-view="report" aria-pressed="true">Report</button>
              <button type="button" data-view="evidence" aria-pressed="false">Evidence</button>
            </div>
```

Add `data-view="report"` to `<section class="stage" id="stage-report" …>`.

- [ ] **Step 3: Insert the Evidence view after the `</div>` that closes `<div class="with-rail report-main">` (Task 7), before `</section>`**

```html
        <!-- ── the Evidence view (Q4) ──────────────────────────────────
             A flat list of findings with status filters, a target tag per row and a detail pane
             in the rail track. Until the service serves GET /research/{id}/evidence (api-gaps E1)
             the real app shows one muted line here; the prototype renders the target state from a
             fixture in E1's shape. -->
        <div class="with-rail evidence-view" id="evidenceView">
          <div class="stack" style="gap:var(--space-4)">
            <div class="chip-filters" id="evChips" role="group" aria-label="Filter evidence by status"></div>
            <ol class="ev-list" id="evList" role="listbox" aria-label="Evidence" tabindex="0"></ol>
            <p class="avail" id="evEmpty" hidden>not served by the service yet</p>
          </div>
          <aside class="rail ev-detail card stack-2" id="evDetail" aria-label="Evidence detail" aria-live="polite"></aside>
        </div>
```

- [ ] **Step 4: Append CSS before `</style>`**

```css
/* ═══ 2026-09-26: the Evidence view ═══ */
#stage-report[data-view="evidence"] .report-main{display:none}
#stage-report:not([data-view="evidence"]) .evidence-view{display:none}
.seg-view{flex:none}
.seg-view button{flex:none;min-width:84px}
.chip-filters{display:flex;gap:var(--space-2);flex-wrap:wrap}
.chip-filter{
  display:inline-flex;align-items:center;gap:6px;height:30px;padding:0 var(--space-3);
  border:1px solid var(--border);border-radius:var(--radius-pill);font-size:var(--text-sm);color:var(--muted);
  transition:background var(--motion-fast) var(--ease-standard),color var(--motion-fast) var(--ease-standard);
}
.chip-filter:hover{background:var(--border-soft);color:var(--fg)}
.chip-filter[aria-pressed="true"]{background:var(--surface);color:var(--fg);box-shadow:var(--elev-ring)}
.chip-filter .n{font-family:var(--font-mono);font-size:var(--text-xs);color:var(--meta)}
.ev-list{list-style:none;margin:0;padding:0;display:flex;flex-direction:column;gap:var(--space-2)}
.ev-list:focus-visible{outline:2px solid var(--accent);outline-offset:4px;border-radius:var(--radius-md)}
.ev-row{
  display:grid;grid-template-columns:auto auto minmax(0,1fr) auto;gap:var(--space-3);align-items:center;
  padding:var(--space-3);border:1px solid var(--border);border-radius:var(--radius-md);background:var(--surface);cursor:pointer;
}
.ev-row:hover{border-color:var(--muted)}
.ev-row[aria-selected="true"]{border-color:var(--fg)}
.ev-row .lbl{font-family:var(--font-mono);font-size:var(--text-xs);color:var(--muted)}
.ev-row .txt{font-size:var(--text-sm);color:var(--fg);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;min-width:0}
.ev-row .tags{display:flex;gap:6px;flex-wrap:wrap;justify-content:flex-end}
.pill{font-family:var(--font-mono);font-size:var(--text-xs);border:1px solid var(--border);border-radius:var(--radius-sm);padding:1px 6px;color:var(--muted);white-space:nowrap}
.pill[data-status="verified"],.pill[data-status="verified_corrected"]{color:var(--status-ok);border-color:var(--status-ok)}
.pill[data-status="quoted"],.pill[data-status="not_found"]{color:var(--status-warn);border-color:var(--status-warn)}
.pill[data-status="dropped"],.pill[data-status="refused"]{color:var(--status-danger);border-color:var(--status-danger)}
.tag{font-family:var(--font-mono);font-size:var(--text-xs);border:1px solid var(--border);border-radius:var(--radius-sm);padding:1px 6px;color:var(--fg);white-space:nowrap}
.tag.soft{color:var(--muted)}
.ev-detail .ev-title{font-family:var(--font-display);font-size:var(--text-lg);font-weight:600;margin:0}
.ev-detail .ev-status{font-size:var(--text-sm);color:var(--muted)}
.ev-detail blockquote{margin:0;padding-left:var(--space-3);border-left:2px solid var(--border);font-size:var(--text-sm);color:var(--fg)}
.ev-detail .kv dd{text-align:left}
.ev-detail ul{margin:0;padding-left:var(--space-5);font-size:var(--text-sm)}
.ev-detail .ev-cited button{font-family:var(--font-mono);font-size:var(--text-xs);color:var(--fg);border:1px solid var(--border);border-radius:var(--radius-sm);padding:1px 6px}
.ev-detail .ev-cited button:hover{border-color:var(--muted)}
@media (max-width:1120px){ .ev-detail{position:static} }
```

- [ ] **Step 5: Add the Evidence fixture and view code after `populateReport` (before `HALT_HEADLINES`)** — every value below is one the engine can produce: `attribution` ∈ own | relayed | unattributed, `kind` ∈ actual | forecast (`utils/types.py:324`, `:355`); `quoted` and `dropped` findings carry no figures and are never flagged `context_unchecked` (the Context Check runs only on figure-bearing findings, `evidence_verifier.py:903-917`), so the flag sits on `F06`, a verified finding with a kept figure; a kept figure names its organisation

```js
  /* ═══ the Evidence view (Q4) ═══
     One fixture in E1's shape (spec §4.5), for the one battery-storage report every finished
     session reuses: at least one finding per status, one not-found target (T04) and one refused
     sentence. F03's source scores 0.80 overall, which paints yellow on purpose (DESIGN.md §3.6). */
  var EVIDENCE = {
    "default": {
      session_id:null, iteration:0,
      findings:[
        { label:"F01", status:"verified", dropped_reason:null, context_unchecked:false, cited:true, target_ids:["T01"],
          content:"2,600 GW of proposed U.S. generation and storage projects were waiting for grid access at the end of 2023.",
          snippet:"As of the end of 2023, 2,600 GW of proposed generation and storage projects were waiting for grid access, with typical wait times stretching to five years or more.",
          passage:"The report notes that as of the end of 2023, 2,600 GW of proposed generation and storage projects were waiting for grid access, with typical wait times stretching to five years or more, and that fewer than a quarter of queued projects reach completion.",
          source:{ url:"https://www.novoco.com/", title:"Resolving the Interconnection Queue Bottleneck", organisation:"Novogradac", evaluation_status:"scored", low_confidence:false,
                   authority_score:0.62, recency_score:0.71, relevance_score:0.93, overall_score:0.74 },
          figures:[{ value:"2,600 GW", kept:true, period:"end of 2023", scope:"proposed U.S. generation and storage awaiting grid access", organisation:"Lawrence Berkeley National Laboratory",
                     attribution:"relayed", kind:"actual", release:"2024 queue study", evidence_words:"2,600 GW of proposed generation and storage projects", corrected:false, dropped_reason:null, reason:null }] },
        { label:"F02", status:"verified_corrected", dropped_reason:null, context_unchecked:false, cited:true, target_ids:["T03"],
          content:"China's average share of mineral supply, excluding rare earths, rose to 72 percent in 2025 from 70 percent in 2023.",
          snippet:"Excluding rare earths, the average share of mineral supply held by China rose to 72% in 2025 from 70% in 2023.",
          passage:null,
          source:{ url:"https://crsreports.congress.gov/", title:"Critical Minerals and Materials for Selected Energy Technologies", organisation:"Congressional Research Service", evaluation_status:"scored", low_confidence:false,
                   authority_score:0.91, recency_score:0.84, relevance_score:0.88, overall_score:0.89 },
          figures:[{ value:"72%", kept:true, period:"2025", scope:"average share of mineral supply held by China, excluding rare earths", organisation:"Congressional Research Service",
                     attribution:"own", kind:"actual", release:"R48149, 2025", evidence_words:"rose to 72% in 2025", corrected:true, dropped_reason:null, reason:"the draft read 2024; the page says 2025" }] },
        { label:"F03", status:"quoted", dropped_reason:null, context_unchecked:false, cited:true, target_ids:["T03"],
          content:"The IEA estimates that full implementation of China's export controls could put $6.5 trillion in annual production at risk.",
          snippet:"The IEA estimates that full implementation of China's export controls could put $6.5 trillion in annual production outside China at risk.",
          passage:null,
          source:{ url:"https://www.aa.com.tr/", title:"Why the IEA says critical minerals have become a $6.5 trillion economic security risk", organisation:"Anadolu Agency", evaluation_status:"scored", low_confidence:false,
                   authority_score:0.74, recency_score:0.90, relevance_score:0.80, overall_score:0.80 },
          figures:[] },
        { label:"F04", status:"dropped", dropped_reason:"snippet_not_on_page", context_unchecked:false, cited:false, target_ids:["T02"],
          content:"The U.S. interconnection queue swelled to a 2,600 GW backlog as of 2026 with median waits approaching five years.",
          snippet:"a 2,600 GW backlog as of 2026 with median waits approaching five years",
          passage:null,
          source:{ url:"https://www.enkiai.com/", title:"Grid Interconnection Delays 2026: A Threat to US Energy", organisation:"Enki AI", evaluation_status:"scored", low_confidence:true,
                   authority_score:0.38, recency_score:0.95, relevance_score:0.82, overall_score:0.67 },
          figures:[] },
        { label:"F05", status:null, dropped_reason:null, context_unchecked:false, cited:false, target_ids:["T01"],
          content:"For every $1 billion in delayed transmission investment, consumers lose between $150 million and $370 million in net benefits per year of delay.",
          snippet:"For every $1 billion in transmission investments that is delayed, consumers lose between $150 million and $370 million in net benefits per year of delay.",
          passage:null,
          source:{ url:"https://www.enkiai.com/", title:"Grid Interconnection Delays 2026: A Threat to US Energy", organisation:"Enki AI", evaluation_status:"scored", low_confidence:true,
                   authority_score:0.38, recency_score:0.95, relevance_score:0.82, overall_score:0.67 },
          figures:[] },
        { label:"F06", status:"verified", dropped_reason:null, context_unchecked:true, cited:true, target_ids:["T03"],
          content:"China processed 70 to 95 percent of global lithium, cobalt, phosphate and graphite in 2024.",
          snippet:"China retained its dominance over the midstream and downstream EV and storage battery supply chain in 2024, processing 70-95% of global lithium, cobalt, phosphate and graphite.",
          passage:null,
          source:{ url:"https://www.iea.org/", title:"Global Critical Minerals Outlook 2025", organisation:"IEA", evaluation_status:"scored", low_confidence:false,
                   authority_score:0.95, recency_score:0.88, relevance_score:0.92, overall_score:0.92 },
          figures:[{ value:"70–95%", kept:true, period:"2024", scope:"share of global lithium, cobalt, phosphate and graphite processed in China", organisation:"IEA",
                     attribution:"own", kind:"actual", release:"Global Critical Minerals Outlook 2025", evidence_words:"processing 70-95% of global lithium, cobalt, phosphate and graphite", corrected:false, dropped_reason:null, reason:null }] }
      ],
      not_found:[
        { target_id:"T04", question:"How do non-U.S. grid connection regimes constrain battery deployment?",
          queries:["EU grid connection queue battery storage 2025", "UK connections reform battery storage wait times"],
          pages_read:["https://www.entsoe.eu/", "https://www.neso.energy/"], searched:true }
      ],
      refused:[
        { where:"Critical minerals and supply-chain concentration",
          text:"Export controls have already removed $6.5 trillion of production from world markets.",
          reason:"The cited finding gives an estimate of production at risk under full implementation, not a realised loss.", finding_labels:["F03"] }
      ]
    }
  };
  function evidenceFor(session){ return EVIDENCE[session.id] || EVIDENCE["default"]; }

  var EV_FILTERS = [
    { id:"all",                label:"All" },
    { id:"verified",           label:"Verified" },
    { id:"verified_corrected", label:"Corrected" },
    { id:"quoted",             label:"Quoted" },
    { id:"dropped",            label:"Dropped" },
    { id:"not_found",          label:"Not found" },
    { id:"refused",            label:"Refused" }
  ];
  var PILL_TEXT = { verified:"verified", verified_corrected:"corrected", quoted:"quoted", dropped:"dropped", not_checked:"not checked", not_found:"not found", refused:"refused" };
  /* The evidence log's verbs (report.py:1931-1935). */
  var VERIFICATION_TEXT = { verified:"verified", verified_corrected:"verified with corrections", quoted:"quoted (snippet found on the page; not checked for context)", not_checked:"not checked" };
  var ev = { rows:[], filter:"all", selected:0, sessionId:null };

  function evidenceRows(data){
    var rows = [];
    data.findings.forEach(function(f){ rows.push({ kind:"finding", status: f.status || "not_checked", id:f.label, item:f }); });
    data.not_found.forEach(function(t){ rows.push({ kind:"not_found", status:"not_found", id:t.target_id, item:t }); });
    data.refused.forEach(function(r, i){ rows.push({ kind:"refused", status:"refused", id:"R" + String(i + 1).padStart(2, "0"), item:r }); });
    return rows;
  }
  function evVisible(){
    return ev.filter === "all" ? ev.rows : ev.rows.filter(function(r){ return r.status === ev.filter; });
  }
  function firstSentence(s){
    var m = /^(.*?[.!?])(\s|$)/.exec(s || "");
    return m ? m[1] : (s || "");
  }
  function el(tag, cls, textContent){
    var e = document.createElement(tag);
    if(cls) e.className = cls;
    if(textContent !== undefined && textContent !== null) e.textContent = textContent;
    return e;
  }
  function pill(status){
    var p = el("span", "pill", PILL_TEXT[status] || status);
    p.setAttribute("data-status", status);
    return p;
  }
  function renderChips(){
    var host = document.getElementById("evChips");
    if(!host) return;
    host.textContent = "";
    EV_FILTERS.forEach(function(f){
      var count = f.id === "all" ? ev.rows.length : ev.rows.filter(function(r){ return r.status === f.id; }).length;
      var b = el("button", "chip-filter");
      b.type = "button";
      b.setAttribute("data-filter", f.id);
      b.setAttribute("aria-pressed", String(ev.filter === f.id));
      b.appendChild(document.createTextNode(f.label + " "));
      b.appendChild(el("span", "n", String(count)));
      b.addEventListener("click", function(){ ev.filter = f.id; ev.selected = 0; renderChips(); renderEvList(); });
      host.appendChild(b);
    });
  }
  function renderEvList(){
    var host = document.getElementById("evList");
    var empty = document.getElementById("evEmpty");
    if(!host) return;
    host.textContent = "";
    var rows = evVisible();
    if(empty) empty.hidden = rows.length > 0;
    rows.forEach(function(r, i){
      var li = el("li", "ev-row");
      li.setAttribute("role", "option");
      li.setAttribute("data-kind", r.kind);
      li.setAttribute("data-status", r.status);
      li.setAttribute("data-id", r.id);
      li.setAttribute("aria-selected", String(i === ev.selected));
      li.appendChild(pill(r.status));
      li.appendChild(el("span", "lbl", r.id));
      var tags = el("span", "tags");
      if(r.kind === "finding"){
        li.appendChild(el("span", "txt", firstSentence(r.item.snippet)));
        (r.item.target_ids || []).forEach(function(t){ tags.appendChild(el("span", "tag", t)); });
        if(r.item.context_unchecked) tags.appendChild(el("span", "tag soft", "context unchecked"));
      } else if(r.kind === "not_found"){
        li.appendChild(el("span", "txt", r.item.question));
        tags.appendChild(el("span", "tag soft", r.item.searched
          ? r.item.queries.length + " queries · " + r.item.pages_read.length + " pages read"
          : "not searched in this run"));
      } else {
        li.appendChild(el("span", "txt", r.item.text));
        tags.appendChild(el("span", "tag soft", r.item.finding_labels.length ? "cited " + r.item.finding_labels.join(", ") : "cited nothing"));
      }
      li.appendChild(tags);
      li.addEventListener("click", function(){ evSelect(i); });
      host.appendChild(li);
    });
    renderEvDetail(rows[ev.selected] || null);
  }
  function evSelect(index){
    var rows = evVisible();
    if(!rows.length) return;
    ev.selected = Math.max(0, Math.min(rows.length - 1, index));
    Array.prototype.forEach.call(document.querySelectorAll("#evList .ev-row"), function(li, i){
      li.setAttribute("aria-selected", String(i === ev.selected));
    });
    renderEvDetail(rows[ev.selected]);
  }
  function evSelectById(id){
    ev.filter = "all";
    renderChips();
    var idx = ev.rows.findIndex(function(r){ return r.id === id; });
    ev.selected = idx < 0 ? 0 : idx;
    renderEvList();
  }
  function kvRow(dl, k, v){
    dl.appendChild(el("dt", null, k));
    dl.appendChild(el("dd", "plain", v));
  }
  function meterRow(label, value){
    var row = el("div", "bar-row");
    row.appendChild(el("span", null, label));
    var bar = el("span", "bar");
    var fill = el("span");
    fill.style.width = (typeof value === "number") ? Math.round(value * 100) + "%" : "0%";
    bar.appendChild(fill);
    row.appendChild(bar);
    var n = el("span", "n", fmtScore(value) === null ? "not scored" : fmtScore(value));
    if(fmtScore(value) === null) n.classList.add("avail");
    row.appendChild(n);
    return row;
  }
  function renderEvDetail(row){
    var host = document.getElementById("evDetail");
    if(!host) return;
    host.textContent = "";
    if(!row){ host.appendChild(el("p", "avail", "Nothing to show under this filter.")); return; }
    var it = row.item;
    if(row.kind === "finding"){
      host.appendChild(el("h2", "ev-title", it.label + " — " + (it.source && it.source.title ? it.source.title : "untitled source")));
      var verb = it.status === "dropped" ? "dropped (" + it.dropped_reason + ")" : (VERIFICATION_TEXT[it.status || "not_checked"]);
      host.appendChild(el("p", "ev-status", verb + (it.context_unchecked ? "; context unchecked" : "")));
      var q = el("div", "stack-2");
      q.appendChild(el("span", "cap", "Snippet"));
      q.appendChild(el("blockquote", null, it.snippet));
      if(it.passage){ q.appendChild(el("span", "cap", "Passage")); q.appendChild(el("blockquote", null, it.passage)); }
      host.appendChild(q);
      if(it.source && it.source.url){
        var a = el("a", "tlink", it.source.title || it.source.url);
        a.href = it.source.url; a.target = "_blank"; a.rel = "noopener";
        var src = el("p", "sm"); src.appendChild(document.createTextNode("Source: ")); src.appendChild(a);
        host.appendChild(src);
      }
      /* The Context Check runs only on figure-bearing findings (evidence_verifier.py:903-917): no figures → it never ran;
         flagged → a batch could not be judged; otherwise the kept figure's confirmed organisation, kind and release. */
      var kept = (it.figures || []).filter(function(f){ return f.kept && f.organisation; })[0];
      host.appendChild(el("p", "sm", "Context check: " + (!(it.figures || []).length ? "not run (no figures)" : it.context_unchecked || !kept
        ? "context unchecked" : [kept.organisation, kept.kind, kept.release].filter(Boolean).join(" · "))));
      var bars = el("div", "bars");
      [["authority", it.source && it.source.authority_score], ["recency", it.source && it.source.recency_score],
       ["relevance", it.source && it.source.relevance_score], ["overall", it.source && it.source.overall_score]].forEach(function(m){
        bars.appendChild(meterRow(m[0], typeof m[1] === "number" ? m[1] : null));
      });
      host.appendChild(bars);
      paintMeters(host);
      if((it.figures || []).length){
        var ul = el("ul", "ev-figures");
        it.figures.forEach(function(f){
          ul.appendChild(el("li", null, f.kept
            ? f.value + ": kept · period " + (f.period || "not stated") + " · scope " + (f.scope || "not stated") + (f.corrected ? " · corrected" : "")
            : f.value + ": dropped (" + f.dropped_reason + ") " + (f.reason || "")));
        });
        host.appendChild(el("span", "cap", "Figures"));
        host.appendChild(ul);
      }
    } else if(row.kind === "not_found"){
      host.appendChild(el("h2", "ev-title", it.target_id + " — not found"));
      host.appendChild(el("p", "ev-status", it.question));
      if(!it.searched){ host.appendChild(el("p", "avail", "not searched in this run")); return; }
      host.appendChild(el("span", "cap", "Searched"));
      var ql = el("ul", "ev-queries");
      it.queries.forEach(function(qq){ ql.appendChild(el("li", null, qq)); });
      host.appendChild(ql);
      host.appendChild(el("span", "cap", "Pages read"));
      var pl = el("ul", "ev-pages");
      it.pages_read.forEach(function(u){
        var li = el("li"); var a = el("a", "tlink", u); a.href = u; a.target = "_blank"; a.rel = "noopener"; li.appendChild(a); pl.appendChild(li);
      });
      host.appendChild(pl);
    } else {
      host.appendChild(el("h2", "ev-title", row.id + " — refused sentence"));
      host.appendChild(el("blockquote", null, it.text));
      var cited = el("p", "ev-cited sm");
      cited.appendChild(document.createTextNode(it.finding_labels.length ? "cited " : "cited nothing"));
      it.finding_labels.forEach(function(lbl){
        var b = el("button", null, lbl); b.type = "button";
        b.addEventListener("click", function(){ evSelectById(lbl); });
        cited.appendChild(b);
      });
      host.appendChild(cited);
      var dl = el("dl", "kv");
      kvRow(dl, "reason", it.reason);
      kvRow(dl, "where", it.where);
      host.appendChild(dl);
    }
  }
  function renderEvidence(session){
    ev.rows = evidenceRows(evidenceFor(session));
    ev.filter = "all";
    ev.selected = 0;
    ev.sessionId = session.id;
    renderChips();
    renderEvList();
  }
  function currentView(){
    return stages.report.getAttribute("data-view") || "report";
  }
  function setView(name){
    name = name === "evidence" ? "evidence" : "report";
    stages.report.setAttribute("data-view", name);
    Array.prototype.forEach.call(document.querySelectorAll("#segView button"), function(b){
      b.setAttribute("aria-pressed", String(b.getAttribute("data-view") === name));
    });
    var s = activeSession();
    if(name === "evidence" && s && ev.sessionId !== s.id) renderEvidence(s);
  }
  (function wireEvidence(){
    var seg = document.getElementById("segView");
    if(seg) seg.addEventListener("click", function(e){
      var b = e.target.closest("button[data-view]"); if(!b) return;
      setView(b.getAttribute("data-view"));
    });
    var list = document.getElementById("evList");
    if(list) list.addEventListener("keydown", function(e){
      if(e.key === "ArrowDown"){ e.preventDefault(); evSelect(ev.selected + 1); }
      if(e.key === "ArrowUp"){ e.preventDefault(); evSelect(ev.selected - 1); }
    });
  })();
```

Then make `populateReport()` (Task 7) end with these two lines, after the Errors block:

```js
    /* the view is per session and not persisted: every open starts on Report */
    ev.sessionId = null;
    setView("report");
```

- [ ] **Step 6: Add the two hook members to `drConsole`** (after `loop`, Task 5):

```js
      view: function(name){ if(name) setView(name); return currentView(); },
      evidence: function(id){ openSession(id); setView("evidence"); }
```

- [ ] **Step 7: Run the check; then the keyboard check**

Run: `node "$TEMP/dr-probe/probe.mjs" docs/design/prototype index.html "$TEMP/dr-probe/t8-evidence.js"` → exit 0, `"ok": true` (`all.length === 8`: six findings + T04 + one refused; F05 shows only under All).

```bash
cat > "$TEMP/dr-probe/t8-keys.js" <<'EOF'
c.evidence("b41e77aa"); await waitFor(() => c.stage() === "report", 3000);
const sel = () => $$('#evList li').findIndex((li) => li.getAttribute('aria-selected') === 'true');
const first = [sel(), text('#evDetail .ev-title')];
$('#evList').dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowDown', bubbles: true }));
const second = [sel(), text('#evDetail .ev-title')];
$('#evList').dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowUp', bubbles: true }));
$('#evList').dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowUp', bubbles: true }));
const clamped = sel();
return { ok: first[0] === 0 && second[0] === 1 && first[1] !== second[1] && clamped === 0, first, second, clamped };
EOF
node "$TEMP/dr-probe/probe.mjs" docs/design/prototype index.html "$TEMP/dr-probe/t8-keys.js"
```

Expected: exit 0.

- [ ] **Step 8: Re-run the report-stage probes (`t7-report.js`, `t7-widths.js`, `t7-finish.js`) — all exit 0 — then commit**

```bash
git add docs/design/prototype/index.html
git commit -m "design(prototype): Evidence view with status filters, target tags and a detail pane; Report | Evidence toggle; drConsole.view/evidence"
```

---

### Task 9: `states.html` — chips, failed panel, partial outcomes, options-table frame (spec §4.6 states row, AC12 options table)

**Files:**
- Modify: `docs/design/prototype/states.html:160-161` (append CSS before `</style>`), `:174-203` (the six chip cards inside `<div class="grid-3">`), `:377-462` (section `state-session-failed`), `:466-566` (section `state-partial-report`). The colour-vision chips in `.cvd` (`:228-231`) are not touched.
- Test: `$TEMP/dr-probe/t9-states.js`

**Interfaces:**
- Consumes: nothing from `index.html` (`states.html` is self-contained with its own `:root`, which is not under AC18 and is not edited).
- Produces: a static page the render script captures whole as `06-states`; `[data-od-id="options-table"]` with `.tbl-frame > table.tbl-options`; `[data-od-id="partial-outcomes"]` with three chips.

- [ ] **Step 1: Write the failing check** — the chip selector is scoped to `.grid-3` so the three `.cvd` colour-vision chips in the same section are not counted

```bash
cat > "$TEMP/dr-probe/t9-states.js" <<'EOF'
const chips = $$('[data-od-id="state-status-mapping"] .grid-3 .chip').map((el) => el.textContent.replace(/\s+/g, ' ').trim());
const wantChips = ['Running · pass 2 of 2', 'Completed · review accepted · 0.86', 'Partially completed · extra passes used · 1 target not found',
  'Partially completed · not accepted · 0.71', 'Partially completed · review unavailable', 'Failed · halted', 'Not available while running', 'Running · stage starting'];
const cvd = $$('[data-od-id="state-status-mapping"] .cvd .chip').length;
const survived = $$('[data-od-id="failed-survived"] dd').map((d) => d.textContent.trim());
const tail = $$('[data-od-id="failed-event-tail"] .log li .etype').map((e) => e.textContent.trim());
const tailSentence = /[Ee]ight frames/.test($('[data-od-id="failed-event-tail"]').textContent);
const headline = text('[data-od-id="failed-panel"] h3.sub');
const partialChips = $$('[data-od-id="partial-outcomes"] .chip').map((el) => el.textContent.replace(/\s+/g, ' ').trim());
const frame = $('[data-od-id="options-table"] .tbl-frame');
const before = { scroll: frame.scrollWidth, client: frame.clientWidth };
frame.scrollLeft = frame.scrollWidth;
await sleep(50);
const cell = frame.querySelector('tbody td:first-child').getBoundingClientRect(), box = frame.getBoundingClientRect();
const pinned = cell.left >= box.left - 0.5 && cell.right <= box.right + 0.5 && getComputedStyle(frame.querySelector('tbody td:first-child')).position === 'sticky';
const contrastRows = $$('[data-od-id="contrast-receipt"] tbody tr').length;
const stale = /Executive summary|Claim verdicts|Quality snapshot|3 of 3 passes|claims checked/.test($('[data-od-id="state-partial-report"]').textContent + $('[data-od-id="state-session-failed"]').textContent);
const ok = JSON.stringify(chips) === JSON.stringify(wantChips) && cvd === 3
  && headline === 'Model provider misconfigured' && $('[data-od-id="failed-panel"]').textContent.includes('graph_provider_configuration_error')
  && survived.length === 7 && survived.every((t) => t === 'not reached')
  && tail.length === 8 && tail[0] === 'graph.session.started' && tail.filter((t) => t === 'graph.node.skipped').length === 5 && tail[7] === 'graph.session.completed' && tailSentence
  && partialChips.length === 3 && before.scroll > before.client && pinned && contrastRows === 8 && !stale && document.scrollingElement.scrollWidth <= innerWidth;
return { ok, chips, cvd, headline, survived, tail, tailSentence, partialChips, before, pinned, contrastRows, stale };
EOF
node "$TEMP/dr-probe/probe.mjs" docs/design/prototype states.html "$TEMP/dr-probe/t9-states.js"
```

Expected: exit 1 (`chips[0]` is `Running · iteration 2 of 3`; `[data-od-id="options-table"]` is null → `error`).

- [ ] **Step 2: Append CSS before `</style>` (`:160`)**

```css
/* ═══ 2026-09-26: tables in a scrolling frame; the options table pins its first column ═══ */
.tbl-frame{overflow-x:auto;max-width:100%}
.tbl-options{min-width:760px}
.tbl-options th:first-child,.tbl-options td:first-child{position:sticky;left:0;background:var(--surface);z-index:1}
.tbl-options tbody tr:hover td:first-child{background:var(--surface)}
.tbl-foot{font-size:var(--text-sm);color:var(--muted);padding-top:var(--space-3)}
.kv dd.avail{font-family:var(--font-body);color:var(--muted)}
```

- [ ] **Step 3: Replace the six chip cards (`:174-203`, the `<div class="card stack-2" data-od-id="chip-…">` blocks inside `.grid-3`)**

```html
        <div class="card stack-2" data-od-id="chip-running">
          <span class="cap">API status <span class="mono">running</span></span>
          <span class="chip chip-lg"><span class="dot dot-ok"></span>Running <span class="kind">· pass 2 of 2</span></span>
          <p class="avail">Text label always present. The pass is read from <span class="mono">graph.node.started</span> and <span class="mono">graph.extra_pass.started</span>; the ceiling is 1 + <span class="mono">max_extra_passes</span>.</p>
        </div>
        <div class="card stack-2" data-od-id="chip-completed">
          <span class="cap">API status <span class="mono">completed</span></span>
          <span class="chip chip-lg"><span class="dot dot-ok"></span>Completed <span class="kind">· review accepted · 0.86</span></span>
          <p class="avail"><span class="mono">completed</span> is exactly the route <span class="mono">report_accepted</span>; the score is <span class="mono">semantic_review_score</span>, two decimals.</p>
        </div>
        <div class="card stack-2" data-od-id="chip-partial">
          <span class="cap">API status <span class="mono">max_iterations</span> · <span class="mono">incomplete</span></span>
          <span class="chip chip-lg"><span class="dot dot-warn"></span>Partially completed <span class="kind">· extra passes used · 1 target not found</span></span>
          <span class="chip chip-lg"><span class="dot dot-warn"></span>Partially completed <span class="kind">· not accepted · 0.71</span></span>
          <span class="chip chip-lg"><span class="dot dot-warn"></span>Partially completed <span class="kind">· review unavailable</span></span>
          <p class="avail">Same visual weight as Completed. The clause names the outcome: the extra-pass ceiling was spent, a scored review did not accept (or a gate blocked acceptance), or no review score exists.</p>
        </div>
        <div class="card stack-2" data-od-id="chip-failed">
          <span class="cap">API status <span class="mono">failed</span></span>
          <span class="chip chip-lg"><span class="dot dot-danger"></span>Failed <span class="kind">· halted</span></span>
          <p class="avail">The halting type is the failed stage's headline, in plain words; the chip says only that the run halted.</p>
        </div>
        <div class="card stack-2" data-od-id="chip-unavailable">
          <span class="cap">Not a status — an absent value</span>
          <span class="chip chip-lg is-unavailable">Not available while running</span>
          <p class="avail">No chip, no dot, no icon, no disabled control. Muted text, and nothing else.</p>
        </div>
        <div class="card stack-2" data-od-id="chip-pending">
          <span class="cap">Before the first frame</span>
          <span class="chip chip-lg"><span class="dot dot-ok"></span>Running <span class="kind">· stage starting</span></span>
          <p class="avail">There is no sixth status. <span class="mono">POST /research</span> returns <span class="mono">running</span> before any event exists.</p>
        </div>
```

- [ ] **Step 4: Rewrite the failed section (`:377-462`, the whole `<section class="demo" data-od-id="state-session-failed" …>`)**

```html
  <!-- ═══ 3 · SESSION FAILED ═══ -->
  <section class="demo" data-od-id="state-session-failed" aria-labelledby="failed-h">
    <div class="demo-head">
      <div class="stack-2">
        <p class="eyebrow">State 3 · session failed</p>
        <h2 class="demo-title" id="failed-h">The run halted, and nothing was published</h2>
        <p class="lead">A failure is a halt mark in state, not an exception out of the run. A halting node records its error and emits no completion; every later agent node is skipped; the reviewer's route is <span class="mono">end</span>, so publication never runs and the report endpoint answers 409, not 404.</p>
      </div>
      <div class="card stack-2">
        <span class="cap">Reached when</span>
        <p class="sm" style="color:var(--fg)">An error type in <span class="mono">HALTING_ERROR_TYPES</span> lands in state. The route to the end is <span class="mono">halted</span> and <span class="mono">graph.session.completed</span> carries <span class="mono">status: "failed"</span>.</p>
      </div>
    </div>

    <div class="grid-2">
      <div class="stack" style="gap:var(--space-5)">
        <div class="failure stack" data-od-id="failed-panel">
          <div class="row-between">
            <span class="chip chip-lg" style="border-color:color-mix(in oklch,var(--status-danger) 44%,var(--border))"><span class="dot dot-danger" aria-hidden="true"></span>Failed <span class="kind">· halted</span></span>
            <span class="avail-mono">session 2ad900b1 · 0m 04s</span>
          </div>
          <hr />
          <div class="stack-2">
            <h3 class="sub">Model provider misconfigured</h3>
            <p class="sm">The model provider is not configured, so the research run stopped.</p>
            <p class="avail">The headline is the halting type in plain words; the enumerated type is the first row below, and the sentence is the API's own <span class="mono">message</span>.</p>
          </div>
          <dl class="kv">
            <dt>error_type</dt><dd>graph_provider_configuration_error</dd>
            <dt>source</dt><dd>graph</dd>
            <dt>recoverable</dt><dd>false</dd>
            <dt>timestamp</dt><dd>17:22:31Z</dd>
            <dt>finished_at</dt><dd>17:22:31Z</dd>
            <dt>report_path</dt><dd class="avail">Not published</dd>
            <dt>trace_url</dt><dd class="plain">smith.langchain.com ↗</dd>
          </dl>
          <hr />
          <div class="row wrap">
            <button class="btn btn-primary" type="button" data-od-id="cta-failed-fix">Fix configuration</button>
            <button class="btn btn-ghost" type="button" data-od-id="cta-failed-trace">Open trace <span aria-hidden="true">↗</span></button>
          </div>
        </div>

        <div class="card stack" data-od-id="failed-survived">
          <h3 class="sub">What survived the halt</h3>
          <p class="eyebrow" style="margin:0">counted from the event stream</p>
          <dl class="kv">
            <dt>sub-topics researched</dt><dd class="avail">not reached</dd>
            <dt>tool calls</dt><dd class="avail">not reached</dd>
            <dt>findings</dt><dd class="avail">not reached</dd>
            <dt>sources scored</dt><dd class="avail">not reached</dd>
            <dt>verified / corrected / dropped</dt><dd class="avail">not reached</dd>
            <dt>sentences / refused</dt><dd class="avail">not reached</dd>
            <dt>review score</dt><dd class="avail">not reached</dd>
          </dl>
          <p class="avail">The running stage's counters, frozen at the halt. A planner halt reaches none of them, so every row reads <span class="mono">not reached</span> — a value never measured, never a zero.</p>
        </div>
      </div>

      <div class="stack" style="gap:var(--space-5)">
        <div class="card stack-2" data-od-id="failed-report-request">
          <h3 class="sub">Asking for the report</h3>
          <pre class="wire">GET /research/2ad900b1/report

<span class="n">409 Conflict</span>
{
  <span class="k">"error"</span>: {
    <span class="k">"code"</span>: <span class="nostr">"report_unavailable"</span>,
    <span class="k">"message"</span>: <span class="nostr">"Research session finished without a report."</span>,
    <span class="k">"issues"</span>: []
  }
}</pre>
          <p class="avail">A finished session with no report is a 409, distinct from the 409 a <em>running</em> session returns (<span class="mono">session_not_complete</span>) and distinct from the 404 an unknown id returns. Three different truths, three different codes, and the screen prints the one it got.</p>
        </div>

        <div class="card stack" data-od-id="failed-event-tail">
          <h3 class="sub">The event tail stops where the run did</h3>
          <ol class="log">
            <li><span class="avail-mono">17:22:27Z</span><span><span class="etype">graph.session.started</span> <span class="avail">max_extra_passes 1.</span></span></li>
            <li><span class="avail-mono">17:22:31Z</span><span><span class="etype">graph.node.started</span> <span class="avail">planner, iteration 0.</span></span></li>
            <li><span class="avail-mono">17:22:31Z</span><span><span class="etype">graph.node.skipped</span> <span class="avail">researcher — halted.</span></span></li>
            <li><span class="avail-mono">17:22:31Z</span><span><span class="etype">graph.node.skipped</span> <span class="avail">source_evaluator — halted.</span></span></li>
            <li><span class="avail-mono">17:22:31Z</span><span><span class="etype">graph.node.skipped</span> <span class="avail">evidence_verifier — halted.</span></span></li>
            <li><span class="avail-mono">17:22:31Z</span><span><span class="etype">graph.node.skipped</span> <span class="avail">report_writer — halted.</span></span></li>
            <li><span class="avail-mono">17:22:31Z</span><span><span class="etype">graph.node.skipped</span> <span class="avail">report_reviewer — halted.</span></span></li>
            <li><span class="avail-mono">17:22:31Z</span><span><span class="etype">graph.session.completed</span> <span class="avail">status failed, error_count 1, has_report false.</span></span></li>
          </ol>
          <p class="avail">Eight frames, then the stream closes. The halting error is not a frame: it is recorded on the session and shown in the failed panel. There is no terminal sentinel event, so the front end learns the run ended when the connection ends and reads <span class="mono">GET /status</span> to learn <em>how</em>. Publishing has no frame at all — the graph never runs <span class="mono">finalize_report</span> after a halt — so the console marks it skipped from <span class="mono">status: "failed"</span>, never from <span class="mono">has_report</span>.</p>
        </div>

        <div class="note" data-od-id="failed-note">
          <div class="note-head"><span class="mk">note</span>Not a crash</div>
          <p>The enumerated halt types are outcomes the engine models deliberately. This screen names the type and offers the two things an operator can actually do: fix the configuration, or read the trace. It does not offer to re-run, because the API has no re-run endpoint — the operator submits a new session.</p>
        </div>
      </div>
    </div>
  </section>
```

- [ ] **Step 5: Rewrite the partial section (`:466-566`, the whole `<section class="demo" data-od-id="state-partial-report" …>`)**

```html
  <!-- ═══ 4 · PARTIAL REPORT ═══ -->
  <section class="demo" data-od-id="state-partial-report" aria-labelledby="partial-h">
    <div class="demo-head">
      <div class="stack-2">
        <p class="eyebrow">State 4 · partial report</p>
        <h2 class="demo-title" id="partial-h">A finished partial run still delivers a report</h2>
        <p class="lead">Three outcomes land here: the extra-pass ceiling was spent with a required target still missing (<span class="mono">max_iterations</span>), a scored review did not accept the report or a quality gate blocked acceptance (<span class="mono">incomplete</span>, scored), or no review score exists (<span class="mono">incomplete</span>, unavailable). In every case the report exists, is authoritative, and says itself what it could not confirm.</p>
      </div>
      <div class="card stack-2">
        <span class="cap">Reached when</span>
        <p class="sm" style="color:var(--fg)"><span class="mono">status</span> is <span class="mono">max_iterations</span> (route <span class="mono">extra_passes_exhausted</span>) or <span class="mono">incomplete</span> (route <span class="mono">report_not_accepted</span> or <span class="mono">review_unavailable</span>). <span class="mono">completed</span> never lands here: it is exactly the route <span class="mono">report_accepted</span>.</p>
      </div>
    </div>

    <div class="grid-2">
      <div class="stack" style="gap:var(--space-5)">
        <div class="card stack" data-od-id="partial-header">
          <div class="row-between">
            <span class="chip chip-lg"><span class="dot dot-warn" aria-hidden="true"></span>Partially completed <span class="kind">· extra passes used · 1 target not found</span></span>
            <span class="avail-mono">session 7c0d13ff · 14m 02s · pass 2 of 2</span>
          </div>
          <hr />
          <h3 class="sub">What limits solid-state electrolyte scale-up for EV cells?</h3>
          <p class="sm">The report is complete as an artifact: bottom line, findings table, parts, sources. What is missing is evidence for one required target, and the report says so under <em>What we couldn't confirm</em>.</p>
          <div class="row wrap">
            <a class="btn btn-primary" href="#" data-od-id="cta-partial-download">Download Report</a>
            <a class="btn btn-ghost" href="#" data-od-id="cta-partial-trace">Open LangSmith trace <span aria-hidden="true">↗</span></a>
          </div>
        </div>

        <div class="card stack" style="gap:var(--space-4)" data-od-id="partial-outcomes">
          <h3 class="sub">The three partial outcomes, side by side</h3>
          <div class="stack-2">
            <span class="chip chip-lg"><span class="dot dot-warn" aria-hidden="true"></span>Partially completed <span class="kind">· extra passes used · 1 target not found</span></span>
            <span class="chip chip-lg"><span class="dot dot-warn" aria-hidden="true"></span>Partially completed <span class="kind">· not accepted · 0.71</span></span>
            <span class="chip chip-lg"><span class="dot dot-warn" aria-hidden="true"></span>Partially completed <span class="kind">· review unavailable</span></span>
          </div>
          <p class="avail">Same dot, same label, a different clause. The clause is built from <span class="mono">coverage.not_found_target_ids</span>, <span class="mono">semantic_review_status</span> and <span class="mono">semantic_review_score</span>; it is never a constant.</p>
        </div>

        <div class="card stack" style="gap:var(--space-4)" data-od-id="partial-evidence-counts">
          <h3 class="sub">Evidence counts</h3>
          <dl class="kv">
            <dt>findings</dt><dd>18</dd>
            <dt>verified</dt><dd>12</dd>
            <dt>corrected</dt><dd>2</dd>
            <dt>quoted</dt><dd>1</dd>
            <dt>dropped</dt><dd>3</dd>
            <dt>context unchecked</dt><dd>1</dd>
            <dt>cited</dt><dd>11</dd>
            <dt>reads</dt><dd>24 network · 7 cache</dd>
            <dt>unique works</dt><dd>14</dd>
            <dt>publishers</dt><dd>9</dd>
          </dl>
          <p class="avail">Eleven values from <span class="mono">evidence_counts</span>, served on the response once the run ends. When the block is null — a halt before the writer — the card reads <span class="mono">not measured</span>.</p>
        </div>
      </div>

      <div class="stack" style="gap:var(--space-5)">
        <div class="card stack" style="gap:var(--space-4)" data-od-id="partial-report-head">
          <h3 class="sub">The report itself, opening as usual</h3>
          <div class="prose" style="max-width:100%">
            <p class="avail" style="margin-bottom:var(--space-3)">Evidence as of 2026-09-16 · 5 sources</p>
            <p class="cap" style="margin-bottom:var(--space-3)">Bottom line</p>
            <p style="font-size:var(--text-sm);line-height:var(--leading-body);color:var(--fg)">Manufacturing yield at the separator and the cost of lithium-scale purification are the best-evidenced constraints on solid-state scale-up. Recycling economics rests on a single verified source, and dry-electrode processing on none. <a class="cite" href="#">[2][4]</a></p>
            <p class="cap" style="margin:var(--space-4) 0 var(--space-3)">What we couldn't confirm</p>
            <p style="font-size:var(--text-sm);color:var(--muted)">Dry-electrode processing costs: searched twice, three pages read, no verified figure.</p>
          </div>
          <hr />
          <p class="avail">Identical typography to the accepted report. A partial report is not set in a warning colour, not wrapped in a banner, and not truncated. The status chip carries the qualification; the document is left alone.</p>
        </div>

        <div class="card stack" style="gap:var(--space-4)" data-od-id="partial-review-coverage">
          <h3 class="sub">Review and coverage</h3>
          <div class="bars">
            <div class="bar-row">
              <span style="color:var(--fg)">Review score</span>
              <span class="track"><span style="width:74%"></span></span>
              <span class="n">0.74</span>
            </div>
            <div class="bar-row">
              <span style="color:var(--fg)">Scored sources cited</span>
              <span class="track"><span class="ok" style="width:86%"></span></span>
              <span class="n">0.86</span>
            </div>
          </div>
          <dl class="kv">
            <dt>required targets answered</dt><dd>3 of 4</dd>
            <dt>not found</dt><dd>T04</dd>
          </dl>
          <p class="avail">The review meter carries the 0.80 acceptance line; 0.74 sits in the middle band. Coverage names the target ids the run could not answer — ids only, until the evidence endpoint (api-gaps E1) supplies the question text.</p>
        </div>

        <div class="card stack" style="gap:var(--space-4)" data-od-id="options-table">
          <h3 class="sub">An options table, six columns wide</h3>
          <div class="tbl-frame">
            <table class="tbl tbl-options">
              <thead><tr><th>Option</th><th>Energy density</th><th>Cycle life</th><th>Cost per kWh</th><th>Thermal risk</th><th>Recommended by</th></tr></thead>
              <tbody>
                <tr><td class="wrapcell">Lithium iron phosphate</td><td>160 Wh/kg</td><td>6,000 cycles</td><td>$115</td><td>low</td><td class="wrapcell">Three of five sources <span class="cite">[1][3][5]</span></td></tr>
                <tr><td class="wrapcell">Nickel manganese cobalt</td><td>250 Wh/kg</td><td>2,500 cycles</td><td>$139</td><td>moderate</td><td class="wrapcell">One source <span class="cite">[2]</span></td></tr>
                <tr><td class="wrapcell">Sodium-ion</td><td>140 Wh/kg</td><td>4,000 cycles</td><td>not stated</td><td>low</td><td class="wrapcell">One source <span class="cite">[4]</span></td></tr>
              </tbody>
            </table>
          </div>
          <p class="tbl-foot"><em>Options compared on the parts the plan named; an empty cell reads not stated.</em></p>
          <p class="avail">Up to six columns (Option, four parts, Recommended by) cannot fit a 534 px report column, so the table sits in a frame that scrolls sideways while the page does not, and the <span class="mono">Option</span> column stays pinned over the scroll. The findings table (four columns) never needs the frame to overflow.</p>
        </div>

        <div class="note attn" data-od-id="partial-vs-failed">
          <div class="note-head"><span class="mk">rule</span>Partial is not failed</div>
          <p>A partial run produced its deliverable. The screen uses the same layout, the same type scale and the same action ordering as the accepted report, and the difference is carried by one status chip and what the report itself says it could not confirm. Escalating a normal outcome into a warning state would train the operator to ignore the state that matters.</p>
        </div>
      </div>
    </div>
  </section>
```

- [ ] **Step 6: Run the check, then the stale-name command scoped to `states.html`**

Run: `node "$TEMP/dr-probe/probe.mjs" docs/design/prototype states.html "$TEMP/dr-probe/t9-states.js"` → exit 0, `"ok": true` (`chips` has 8 entries, `cvd` is 3).

Run: `rg -n -e 'fact_checker' -e '\bcritic\b' -e '\bCritic\b' -e '[Cc]ritique' -e 'synthesizer' -e 'destination:\s*"?refine' -e 'graph\.refinement' -e '\bRefinements?\b' -e 'refinement (pass|budget|loop|track|request|strip)' -e 'iteration \S+ of' -e '[Cc]orroboration' -e '[Cc]laim confidence' docs/design/prototype/states.html`
Expected: prints nothing (was 6 lines).

- [ ] **Step 7: Commit**

```bash
git add docs/design/prototype/states.html
git commit -m "design(states): chips, failed panel, partial outcomes and an options-table frame for the Evidence Verifier workflow"
```

---

### Task 10: Render script — captures `08-evidence` and `09-running-extra-pass`; regenerate `reference/` (spec §4.6, AC15)

**Files:**
- Modify: `scripts/render_design_reference.mjs:1-11` (header comment), `:138-147` (`const results = [];` and `shot`), `:180-186` (after the failed-stage block, before `// states.html is a static fixture`), `:146`, `:200` and `:210` (the `padEnd(14)` widths and the literal `06-states` label)
- Regenerate: `docs/design/reference/01-idle.png` … `07-idle-phone.png`; create `08-evidence.png`, `09-running-extra-pass.png`

**Interfaces:**
- Consumes: `drConsole.open/view/advanceTo/arc/loop/stage` (Tasks 4, 5, 8).
- Produces: `shot(name, expectedStage, extra?)` where `extra` is an optional `async () => true | string`; the summary still counts captures only, so it ends `9/9 captures verified`.

- [ ] **Step 1: Run the script as the failing test**

Run: `node scripts/render_design_reference.mjs docs/design/prototype docs/design/reference`
Expected: ends with `7/7 captures verified` and no `08-`/`09-` files: `ls docs/design/reference` shows seven PNGs.

- [ ] **Step 2: Replace `:138-147` (from `const results = \[\];` through the closing `}` of `shot` — the block below redeclares `results`, so the old line 138 must go with it) to take an optional extra assertion folded into the capture's verdict**

```js
const results = [];
async function shot(name, expectedStage, extra) {
  const actual = await evaluate('window.drConsole.stage()').catch(() => '?');
  const png = await send('Page.captureScreenshot', { format: 'png', captureBeyondViewport: false });
  const file = path.join(OUT_DIR, `${name}.png`);
  await writeFile(file, Buffer.from(png.data, 'base64'));
  let ok = expectedStage === null || actual === expectedStage;
  let note = '';
  if (ok && extra) {
    const verdict = await extra().catch((e) => String(e.message || e));
    if (verdict !== true) { ok = false; note = ` ${verdict}`; }
  }
  results.push({ name, expected: expectedStage, actual: `${actual}${note}`, ok, file });
  console.log(`${ok ? 'OK  ' : 'FAIL'}  ${name.padEnd(22)} stage=${String(actual).padEnd(10)} expected=${expectedStage ?? '-'}${note}`);
}
```

Widen the `padEnd(14)` inside `shot` (already done above), the one in the summary loop (`:210`) to `padEnd(22)`, and pad the literal label in the `06-states` console line (`:200`) to the same width: `console.log(`OK    ${'06-states'.padEnd(22)} full page ${W}x${fullH}`);`.

- [ ] **Step 3: Add the two captures after the failed-stage block (after the `}` that closes `if (failedId) { … } else { … }`, before `// states.html is a static fixture`)**

```js
// the Evidence view of a finished session (spec §4.6: 08-evidence)
await loadClean('index.html');
await evaluate('window.drConsole.open("b41e77aa")');
if (await waitForStage('report')) {
  await evaluate('window.drConsole.view("evidence")');
  await sleep(900);
  await shot('08-evidence', 'report', async () => {
    const view = await evaluate('window.drConsole.view()');
    return view === 'evidence' ? true : `view()=${view}, expected evidence`;
  });
} else console.log('FAIL  08-evidence            never reached "report"');

// the extra-pass arc, settled, with the loop tag in the header (spec §4.6: 09-running-extra-pass)
await loadClean('index.html');
await evaluate('window.drConsole.open("8f2c1d90")');
if (await waitForStage('running')) {
  await evaluate('window.drConsole.advanceTo("graph.extra_pass.started")');
  await sleep(600);
  await shot('09-running-extra-pass', 'running', async () => {
    const [arc, loop] = await evaluate('[window.drConsole.arc(), window.drConsole.loop()]');
    return arc === 'extra_pass' && loop === 'settled' ? true : `arc()=${arc} loop()=${loop}, expected extra_pass/settled`;
  });
} else console.log('FAIL  09-running-extra-pass  never reached "running"');
```

Update the header comment (`:2-10`): replace `driving all five stages through the page's own` with `driving all five stages, the Evidence view and the extra-pass arc through the page's own`.

- [ ] **Step 4: Run the script; verify the count and the files**

Run: `node scripts/render_design_reference.mjs docs/design/prototype docs/design/reference`
Expected: nine `OK` lines and `9/9 captures verified`; exit 0.

Run: `ls docs/design/reference` → `01-idle.png 02-submitted.png 03-running.png 04-report.png 05-failed.png 06-states.png 07-idle-phone.png 08-evidence.png 09-running-extra-pass.png`.

Open `docs/design/reference/09-running-extra-pass.png` and `08-evidence.png` (any image viewer) and confirm by eye: the amber arc from Reviewing to Researching with the `extra pass` tag under the blurb; the Evidence list with seven filter chips and the detail pane on the right. Read the Chrome version for the commit body with PowerShell: `(Get-Item 'C:/Program Files/Google/Chrome/Application/chrome.exe').VersionInfo.ProductVersion` → `153.0.8010.53` on this machine.

- [ ] **Step 5: Commit**

```bash
git add scripts/render_design_reference.mjs docs/design/reference
git commit -m "design(reference): nine asserted captures, adding the Evidence view and the settled extra-pass arc

Rendered with Google Chrome 153.0.8010.53, Node v24.13.1."
```

---

### Task 11: `DESIGN.md`, part 1 — header through §3.6 (spec §5 change map rows for `:1-802`, AC17)

**Files:**
- Modify: `docs/design/DESIGN.md` — only the regions named below by today's line numbers (header, §1, §2, §3 table and prose, §3.0, §3.2, the budget heading and paragraphs, the `:482-484` phrase, §3.4's `loop` row and pacing paragraph, §3.5, §3.6). Everything else in `:1-802` stays byte-identical; `:803-1471` is Task 12's.
- Test: `$TEMP/dr-probe/frozen.mjs`, the stale-name command over the file's first part

**Interfaces:**
- Consumes: the finished prototype (Tasks 1–10): fixture ids, `drConsole` members, counter rows, arcs, the composer's request body.
- Produces: DESIGN.md §3.0/§3.2/§3.5 wording (`pass p of P`, the five strip chips, the two arcs, the loop tag) that Task 12's §4–§7 and Task 13's api-gaps quote.

- [ ] **Step 1: Write the frozen-region check and run it with the scoped stale-name count as the failing pair**

```bash
cat > "$TEMP/dr-probe/frozen.mjs" <<'EOF'
// Throwaway: DESIGN.md regions the spec freezes must be byte-identical to the merge commit (AC17).
import { execSync } from 'node:child_process';
import { readFileSync } from 'node:fs';
const norm = (s) => s.replace(/\r\n/g, '\n');
const old = norm(execSync('git show 2010c1d:docs/design/DESIGN.md', { encoding: 'utf8' })).split('\n');
const cur = norm(readFileSync('docs/design/DESIGN.md', 'utf8'));
const ranges = { '§3.1': [210, 277], 'popover rules (before the phrase)': [459, 481], 'popover rules (after the phrase)': [485, 490], '§3.3': [525, 584],
  '§3.6 bands table + boundaries': [778, 789], '§3.6 tail of the anecdote onward': [791, 800], '§5.1–5.3': [967, 1093], '§5.5–5.6': [1151, 1360] };
let bad = 0;
for (const [name, [a, b]] of Object.entries(ranges)) {
  const seg = old.slice(a - 1, b).join('\n');
  const ok = cur.includes(seg);
  console.log(`${ok ? 'OK  ' : 'FAIL'} ${name} (${a}-${b}, ${b - a + 1} lines)`); if (!ok) bad++;
}
const phrase = cur.includes('the read-only\neffort line and its thinking-off text') && !cur.includes('control explaining why it is unavailable when thinking is off');
console.log(`${phrase ? 'OK  ' : 'FAIL'} :482-484 phrase edit`); if (!phrase) bad++;
process.exit(bad ? 1 : 0);
EOF
node "$TEMP/dr-probe/frozen.mjs"
awk '/^## 4\. Status mapping/{exit} {print}' docs/design/DESIGN.md | rg -c -e 'fact_checker' -e '\bcritic\b' -e '\bCritic\b' -e '[Cc]ritique' -e 'synthesizer' -e 'destination:\s*"?refine' -e 'graph\.refinement' -e '\bRefinements?\b' -e 'refinement (pass|budget|loop|track|request|strip)' -e 'iteration \S+ of' -e '[Cc]orroboration' -e '[Cc]laim confidence'
```

Expected before: `frozen.mjs` prints eight `OK` ranges then `FAIL :482-484 phrase edit`, exit 1; the `rg -c` over the text before `## 4. Status mapping` prints a positive count (the stale names in §1–§3.6).

- [ ] **Step 2: Header (`:1-21`)** — after line 8 (`qualification.`) insert a blank line and:

```markdown
*Updated 2026-09-26 to the Evidence Verifier pipeline (f27ac7e); the 2026-09-16 design is otherwise unchanged.*
```

After line 20 (`- \`src/deep_research/utils/types.py\` — …`) append:

```markdown
- `src/deep_research/graph/events.py` — the graph's own progress events: node lifecycle, route decisions, extra passes, redrafts, publication
- `src/deep_research/agents/report.py` — the consumer report and the evidence log, as published
- `docs/superpowers/specs/2026-09-24-evidence-verifier-pipeline-design.md` and `docs/superpowers/specs/2026-09-25-consumer-report-format.md` — the workflow this revision matches
```

- [ ] **Step 3: §1 facts 2 and 4 (`:31-34`, `:40-45`)**

```markdown
2. **A partially completed run is a normal outcome.** `max_iterations` and
   `incomplete` are first-class terminal statuses that still produce a report.
   `max_iterations` now means the extra-pass ceiling was spent with a required
   target still missing; `incomplete` means the review did not accept the report,
   a quality gate blocked acceptance, or no review score exists. The interface
   must not present them as failures, and must not present them as full
   successes either.
```

```markdown
4. **Absence is meaningful.** `trace_url` is `null` while running; `report_path`
   is `null` for a run that published nothing; token usage is not in the API
   response at all; `evidence_counts` is `null` when a run left neither a
   composition nor a quality snapshot; `semantic_review_score` `null` means no
   score exists, never a score of zero. Every one of those renders as muted text,
   never as `0`, `—`, `null`, a placeholder, or a disabled control.
```

- [ ] **Step 4: §2 — A's description (`:56-59`) and last bullet (`:70-71`), B's row examples (`:75-76`), C (`:91-108`), the recommendation (`:112-125`)**

A's first paragraph becomes:

```markdown
The centre column is the report as a document, in the server's own order: the
bottom line, the findings or options table, the parts with their cited points,
what could not be confirmed, the sources. A details rail beside it carries the
review score, coverage, the evidence counts and the session facts, and can swap
to source scores and figure checks for the passage currently in view.
```

A's last bullet: `spot-checks a claim against the rail` → `spot-checks a finding against the rail`.

B's example list (`:75-76`) becomes `graph.node.started`, `researcher.tool_call`, `evidence_verifier.verification.completed`, `graph.route.decided`.

C, whole section:

```markdown
### C. Evidence ledger — findings against their sources

A flat list of every finding the run kept or dropped, filtered by status —
verified, corrected, quoted, dropped, not found, refused — with the required
target it serves as a tag on each row, and a detail pane beside it for the
selected row: the snippet and passage, the source's authority, recency,
relevance and overall scores, the Context Check's organisation, kind and
release, and each figure kept or dropped with its reason. A not-found target
shows the queries searched and the pages read; a refused sentence shows why it
was kept out of the report.

- **Optimises** for verification. A finding and the page it came from are
  visible at once, a dropped snippet or an unchecked context is impossible to
  miss, and source quality stops being a footnote. It is the strongest answer to
  "should I believe this", which is the actual question behind most runs.
- **Costs** reading. The list is not prose, so the narrative the report was
  written to deliver has to be read in A. A finding cited from three places has
  one row, not three, and the pane's value collapses if the list and the pane
  are stacked on a narrow screen.
- **Suits** the operator auditing a report they did not commission, or checking
  why a required target was never answered.
```

Recommendation, first paragraph (`:112-120`): replace `is claim-linked, and the ledger is the only layout that renders the claims and sources the run actually recorded` with `is finding-linked, and the ledger is the only layout that renders the findings and sources the run actually recorded` (one clause; a deliberate correctness edit beyond the spec's "second paragraph", recorded here). The second paragraph (`:122-125`) becomes:

```markdown
The prototype implements A for the investigation state and C as the **Evidence**
view of the same session — a `Report | Evidence` toggle in the report's head bar.
It deliberately does **not** render the event stream as a surface of its own:
continuous event detail is observability the operator rarely reads, and B's
contribution is reduced to the stage spine and the counters block inside the
pipeline card (§5.8).
```

- [ ] **Step 5: §3 table row 4 (`:140`) and the pipeline paragraph (`:161-169`)**

```markdown
| 4 | **Report** | any terminal status with a report — `completed`, or the three partial outcomes: the extra-pass ceiling spent (`max_iterations`), a review that did not accept or a gate that blocked acceptance (`incomplete`, scored), no review score (`incomplete`, unavailable) | The question, the settings in force, actions, then the server's Markdown body and its rail — or the Evidence view | Opening another session, or New research |
```

Append one sentence to the paragraph that starts `**The pipeline owns the running stage.**` (after `read \`Not yet\` for the whole run.`):

```markdown
The counters that used to sit in that column now live inside the pipeline card,
under its header: they update once per node step, because that is how the stream
delivers events (§5.7), and each reads `not yet` until its node finishes.
```

- [ ] **Step 6: §3.0 (`:199-208`)**

```markdown
### 3.0 The settings strip

Every stage after submission carries the same five facts, in the same order, as
a mono chip row: **model, thinking, effort, extra passes, out** — `model
deepseek-flash` · `thinking enabled` · `effort per agent` · `extra passes 1` ·
`out output/`. They are rendered from the session's own copy, not from the live
composer state, because a session's settings are fixed when it is created — the
override dict is read once by `prepare_research_settings` — and the API echoes
nothing back (api-gaps 1.3). When thinking is `disabled` the strip reads `effort
not sent` rather than showing a value that has no effect: the resolver sends no
effort at all in that mode (`providers/capabilities.py:77`). Effort is otherwise
`per agent`, because every role carries its own configured value (§3.2).
```

- [ ] **Step 7: §3.2 controls table and effort paragraph (`:441-455`)** — replace from `What the composer sends — the four knobs it exposes:` through `impossible in the UI is cheaper than a \`422\` the operator has to decode.` with:

````markdown
What the composer sends — the four knobs it exposes, and the one line it only
states:

| Control | Sent as | Constraint the UI enforces |
|---|---|---|
| Model | `config_overrides.llm.model` | `deepseek-flash` (default, the configured model), `deepseek-v4-flash` or `deepseek-v4-pro`, from the capability registry (`providers/capabilities.py:74`) |
| Thinking | `config_overrides.llm.thinking_mode` | `enabled` or `disabled` |
| Effort | nothing | **read-only line**: `effort per agent: planner max · reviewer max · others high`, or `effort: not sent (thinking disabled)` |
| Extra passes | top-level `max_iterations` | **0–2**, default **1**; the stepper's buttons disable at both bounds; `0` is sent as `0`, never omitted |
| Output directory | `config_overrides.output.directory` | Free text |

The request body, exactly:

```json
{"query": "…", "max_iterations": 1, "output_format": "markdown",
 "config_overrides": {"llm": {"model": "deepseek-flash", "thinking_mode": "enabled"},
                      "output": {"directory": "output/"}}}
```

Effort is not a control, and the reasons are recorded here rather than in the
panel. Every role carries its own `reasoning_effort` in `llm.model_overrides`
(`config.yaml:32-63`: planner `max`, report_reviewer `max`, the other four
`high`), and a per-role value wins over `llm.reasoning_effort`
(`utils/config.py:73-75`, `:85-107`) — so a global
`{"llm": {"reasoning_effort": "max"}}` override is accepted and silently changes
nothing. A per-agent override is worse: `llm.model_overrides` entries are
replaced whole (`config.py:664-686`), so
`{"llm": {"model_overrides": {"researcher": {"reasoning_effort": "max"}}}}` drops
the researcher's `timeout`. With thinking `disabled` the DeepSeek capability
declares `disabled_effort = None` (`capabilities.py:77`) and no effort is sent at
all. The line states what is in force; nothing in the panel invites the operator
to change it, and api-gaps lists per-agent effort editing among the gaps the
front end should not close.

The provider is not a control either. `{"llm": {"provider": "openai"}}` is
accepted by validation, but nothing lists the valid provider/model/mode/effort
combinations (api-gaps 1.4), so the composer offers the configured provider's
three models and says nothing about others.
````

- [ ] **Step 8: The budget heading (`:457`), the `:482-484` phrase, the stepper paragraphs (`:491-516`), the output/provider paragraph (`:518-523`)**

Line 457: `### The refinement budget` → `### The extra-pass budget`.

Lines 482–484 (keep 459–481 and 485–490 untouched, byte for byte):

```markdown
enough to fall off the screen. One sentence survives inside the panel: the read-only
effort line and its thinking-off text, which is state rather
than plumbing.
```

Lines 491–516 (from `` `max_iterations` is the operator's control `` through `fact all agree.`):

```markdown
`max_iterations` is the operator's control over how long a run may take, so it is
surfaced as a **bounded stepper** rather than a segmented row or a free number
field. A segmented row would imply a short closed set; a number field would accept
values the run cannot honour. The stepper disables its minus at the floor and its
plus at the ceiling, so the bound is *visible* rather than silently applied by a
clamp.

- **Floor is 0**, which is the schema's own: `max_iterations` is validated `ge=0`
  (`api/models.py:48`), and zero is a real budget — no extra pass, ever. It is
  sent as `0`, never dropped as an absent value.
- **Ceiling is 2**, and that is a UI decision rather than a server one. The field
  has no upper bound, so the ceiling is chosen because each extra pass can add a
  large share of a 70–110 minute run and 2 is the largest budget this console can
  present honestly. It is one constant to raise.
- **Default is 1**, matching the configured `graph.max_extra_passes`
  (`config.yaml:211`); the API passes `max_iterations` to the graph as
  `max_extra_passes` (`api/app.py:183`).

The value is a ceiling, not a target, and the loop is not a retry: an extra pass
is bought only when a required evidence target still has no verified finding
after the review, and only while budget remains (`graph/state.py:295-297`). A run
whose targets are all answered publishes after one pass however high the budget
is set. This is the distinction §3.5 is built around, and it is why the control
is labelled "Extra passes" — the passes beyond the first — rather than
"iterations" with a maximum.

**The chosen budget drives everything downstream.** On submit it becomes the
session's pass ceiling `P = 1 + budget`, the topbar chip counts to it
(`pass 1 of 2`), the pipeline's `pass p of P` reads it, and the report's pass
fact reports the pass the run actually ended on. A budget of 0 reads
`pass 1 of 1` everywhere. A run submitted from the composer plays a one-pass
accepted script at any budget: the loop is a property of the run, and a run the
reviewer accepts first time takes none.
```

Lines 518–523:

```markdown
Output format is **not** surfaced. The API accepts `output_format`, so this is a
product decision rather than a gap: `output.default_format: markdown` is used, and
it is the only value the schema accepts today. The provider is not surfaced
either: an `llm.provider` override is accepted by validation, but no route lists
which providers, models, thinking modes and efforts go together, so the composer
offers the configured provider's models and says so rather than presenting
options that would fail at the first call.
```

- [ ] **Step 9: §3.4 — the `loop` row (`:627`) and the pacing paragraph (`:656-661`)**

```markdown
| `loop` | as `done` | `--status-ok` | none | re-armed by an extra pass or a redraft, and completed again |
```

```markdown
**Pacing is a prototype concern, not a design one.** A real run takes minutes and
each step holds its highlight for as long as it actually runs; on the real stream
a node's events arrive together, once per node step, when the node finishes
(`graph/orchestrator.py:312-346`). The prototype cannot reproduce either, so it
plays one event per tick, compresses the schedule and weights it per node — the
researcher emits a tool-call event per tool per sub-topic and would otherwise
starve every other step of screen time. The weighting changes when a step is
highlighted, never what it says, and every rule in §3.5 is written so the screen
reads the same whether events arrive one at a time or as a burst.
```

- [ ] **Step 10: §3.5 (`:663-771`, from `### 3.5 Passes, and why the pipeline restarts` through `can therefore show \`iteration\` but not rebuild the track's history.`)**

```markdown
### 3.5 Passes, extra passes and redrafts

A **pass** is one complete run of the seven steps. It is not a retry of a failed
step: after each pass the reviewer scores the report and the graph decides where
to go — `finalize` and publish, `extra_pass` back to Researching for the required
targets that still have no verified finding, or `redraft` back to Writing for a
report with a material defect (`graph/state.py:259-314`). `max_extra_passes`
bounds the extra passes and `MAX_WRITER_REDRAFTS = 1` bounds the redrafts. So a
run's real shape is a loop with two return paths, and the pipeline control is
linear. That mismatch is the single most confusing thing about this screen, and
it needs to be designed for rather than left to emerge.

**The number of passes varies, and the interface must not imply otherwise.** An
extra pass is bought before acceptance is even considered, whenever a required
target is missing and budget remains, so a `completed` run with a not-found
target has spent its extra pass (or had a budget of 0). A run whose targets are
all answered publishes after one pass; a run that keeps missing a target uses
the whole budget and ends `max_iterations`. Both are ordinary, and the same UI
has to read correctly for a one-pass run, an extra-pass run, a redrafted run and
a budget-exhausted run.

**The active row is derived, and it is derived from completions.** On the real
stream a node's `graph.node.started` arrives only when the node has already
finished, in the same burst as its own `graph.node.completed`, so the started
event cannot mark the running row. The "Now" row is the **successor of the last
`graph.node.completed`**: Planning until the planner completes, then
Researching, and so on. The exceptions are keyed on the reviewer's route
decision, which it emits before its own completion (`graph/nodes.py:819-841`):

| Trigger | Active row |
|---|---|
| no `graph.node.completed` yet | 1 Planning |
| `graph.node.completed` for `planner` … `report_writer` | the next row |
| `graph.route.decided` | the destination's row, immediately: `extra_pass` → Researching, `redraft` → Writing, `finalize` → Publishing, `end` → none (the failed stage follows) |
| `graph.node.completed` for `report_reviewer` | **inert after a loop decision** — it neither marks Reviewing `done` nor moves the active row; after `finalize` or `end` it marks Reviewing `done` as any completion does |
| `graph.node.completed` for the hops `extra_pass` / `writer_redraft` | nothing: hops never map to a row |
| `graph.node.completed` for `finalize_report`, or `graph.session.completed` | none; the stage transition follows |

Three things make the loop legible, and **none of them is a sentence**:

1. **Two return arcs are drawn, in the spine's left gutter.** Each leaves the
   Reviewing node and returns to the row the graph re-runs: the **extra-pass
   arc** to Researching, stroked `--warn`, and the **redraft arc** to Writing,
   stroked `--meta`. A dashed overlay travels along the lit arc and an arrowhead
   points into the destination. The arc is measured from the live node positions
   on every layout pass, so it stays attached to the nodes through a resize, a
   wrap, or a font change; the endpoints are resolved from each row's
   `data-stage`, never from list position, so Publishing — which is in neither
   loop — can never be an endpoint.

   Each arc has three states, driven by events rather than by a timer:

   | State | Set by | Reads as |
   |---|---|---|
   | `off` | run start; `graph.session.completed`; the next `graph.route.decided` | a run that has not looped, or whose loop is over |
   | `flowing` | `graph.route.decided` with `destination: extra_pass` or `redraft` | the handoff: dashes travel from Reviewing to the destination |
   | `settled` | `graph.extra_pass.started` / `graph.report.redraft_requested` | the re-armed rows are the ones running; the arc rests lit |

   At most one arc is lit; `#spineWrap` carries `data-arc="extra_pass"|"redraft"`
   beside `data-loop`. A one-pass run never shows either, which is correct —
   nothing looped. `--meta` is a stroke here and never text; the loop tag's amber
   is the text-safe `--status-warn`. Under `prefers-reduced-motion` both arcs
   arrive already lit.

2. **The pipeline is always exactly one pass.** On `graph.route.decided` with
   `destination: extra_pass` rows 2–6 go hollow and Planning keeps `done`; with
   `destination: redraft` rows 5–6 go hollow and rows 1–4 keep `done`. The reset
   fires on the route decision, not on the hop's own event, so the spine is
   already hollow as the arc flows. **And the reviewer's own completion, which
   arrives after its route decision in the same burst, is inert in a looping
   pass.** An earlier build cleared nothing: it set the researcher to `loop` and
   left steps 2–7 marked `done` from the pass before, which made the panel read
   as progress moving *backwards*. The same defect returns in a subtler form if
   the reviewer's completion is allowed to mark Reviewing `done` above the hollow
   rows it just reset — so it is not allowed to.

3. **A step re-armed by a loop carries a `↺` mark**, not a label: on Researching
   for an extra pass, on Writing for a redraft, once the row has completed again.

**The loop's reason is the one sentence, and it lives in the header.** On
`graph.extra_pass.started` a tag `extra pass` (text and border `--status-warn`)
appears under the blurb with `{n} required targets had no verified finding`, `n`
being the event's `targets`; on `graph.report.redraft_requested` a tag `redraft`
(text `--muted`, border `--meta`) with `Reviewer named {n} material defects`. The
tag clears on the next `graph.route.decided` or on `graph.session.completed`. A
redraft does not change `iteration` (`nodes.py:1204`), so the chip stays
`pass 1 of 2` while the grey tag shows; an extra pass advances it
(`nodes.py:1141-1146`).

The **pass track was removed from the running stage.** It was a second
representation of the same fact the arc already carries — how many passes the
run has taken — shown as a dot track in its own card. Once the arc existed, the
card was a duplicate sitting in a rail that should hold only supporting detail.
The arc is now the only drawn statement that a loop is happening, positioned on
the pipeline where the loop actually occurs, and `pass p of P` in the header is
the one counter.

Its two nodes remain in the document as a visually-hidden host, because the
event handling still writes to `#passTrack` and `#passSummary`. Keeping the
nodes is deliberate: the wiring stays intact and the track can return by moving
one `div` back into the layout. The visual language is recorded here in case the
count is ever needed again: one dot per pass the run may take, on a hairline —
hollow before it runs, filled with a halo while it runs, filled and quiet once
done, with the connectors between dots filling as the run advances, and a pass
in flight opening its dot into a ring while an arc is `flowing`, so the two
agree.

### Why this says nothing in prose

An earlier pass explained the loop in text: a paragraph on what a pass is, a line
naming each pass's fate, and a caption defending a step count. That was wrong,
and worth recording so it is not reintroduced.

- **It explains the mechanism instead of showing the state.** A reader who needs
  the paragraph has already been failed by the interface; a reader who does not
  needs it out of the way. The loop is a property of the process, and a process is
  the one thing an in-progress screen can demonstrate rather than describe.
- **It defends a number that should not have needed defending.** A step count
  invites the question "out of how many"; `pass p of P` is the one counter kept,
  because both of its numbers are the run's own.
- **It ages badly.** The explanation described a ceiling as if it were a rule, and
  was wrong for any run that did not take exactly that many passes.

The rule this leaves behind: **state that can be shown is not written.** Text on
this screen is reserved for what cannot be shown — the loop's reason, an
enumerated error type, an absent field, a boundary the operator has to respect.

**The prototype scripts the loop per session** rather than fixing it, because the
loop is a property of the run: one demo session takes an extra pass, one takes a
redraft, and a session submitted from the composer — which has no recorded
outcome yet — plays a one-pass accepted script at whatever budget it was given.

The honest limitation: the API reports `iteration` on the session snapshot but not
the route history, so the arcs and the pass number are driven by
`graph.route.decided`, `graph.extra_pass.started` and
`graph.report.redraft_requested` rather than by the session. Reopening a finished
session can therefore show `pass p of P` but not replay its loops.
```

- [ ] **Step 11: §3.6 — first sentence (`:775-777`), the anecdote line (`:790`), the added note (after `:800`)**

Lines 775–777 become:

```markdown
The meters — the report rail's review score and scored sources cited, and the four source scores
(authority, recency, relevance, overall) in the Evidence detail pane — carry a figure and a bar. The
bar's colour is **derived from the figure**, in one place, rather than set beside it:
```

Line 790 becomes:

```markdown
the first row was green and the other three fell through to the default grey, which read
```

After line 800 (`cannot drift away from the rest of the status language.`) insert a blank line and:

```markdown
**`0.80` paints yellow, and that is recorded on purpose.** Review acceptance is `≥ 0.80`
(`utils/types.py:1131`) while the green band starts *above* `0.80`, so a review score or a
cited-sources ratio of exactly `0.80` sits in the middle band. Two fixtures show it: session
`9ea4c220`'s `Scored sources cited` is 4 of 5 = 0.80, and Evidence finding `F03`'s source scores 0.80
overall. Neither side is to be "fixed": the meter states the operator's bands, the reviewer states its
own threshold, and the status text beside the meter says which outcome the run had.
```

- [ ] **Step 12: Run the checks**

```bash
node "$TEMP/dr-probe/frozen.mjs"
awk '/^## 4\. Status mapping/{exit} {print}' docs/design/DESIGN.md | rg -n -e 'fact_checker' -e '\bcritic\b' -e '\bCritic\b' -e '[Cc]ritique' -e 'synthesizer' -e 'destination:\s*"?refine' -e 'graph\.refinement' -e '\bRefinements?\b' -e 'refinement (pass|budget|loop|track|request|strip)' -e 'iteration \S+ of' -e '[Cc]orroboration' -e '[Cc]laim confidence'
rg -c 'webkit-line-clamp is the refinement' docs/design/DESIGN.md
rg -n -e '^### The extra-pass budget' -e '^### 3\.5 Passes, extra passes and redrafts' -e 'Updated 2026-09-26 to the Evidence Verifier pipeline \(f27ac7e\)' docs/design/DESIGN.md
```

Expected: `frozen.mjs` — nine `OK` lines, exit 0; the scoped `rg -n` prints nothing (the kept `-webkit-line-clamp is the refinement` sentence is in §3.1 and does not match `\bRefinements?\b`); `rg -c` prints `1`; the three headings/notes each print one line.

- [ ] **Step 13: Commit**

```bash
git add docs/design/DESIGN.md
git commit -m "docs(design): DESIGN.md §1–§3.6 describe the Evidence Verifier workflow — composer, extra-pass budget, passes, two arcs, meters"
```

---

### Task 12: `DESIGN.md`, part 2 — §4 through §7 (spec §5 change map rows for `:804-1471`, AC17)

**Files:**
- Modify: `docs/design/DESIGN.md` — §4 table and rules, Derived stage display, `iteration` in two places, Error rendering, one sentence in §5.4, §5.7, §5.8, §6, §7. `:967-1093` (§5.1–5.3) and `:1151-1360` (§5.5–5.6) stay byte-identical.
- Test: `$TEMP/dr-probe/frozen.mjs` (Task 11), the stale-name command over the file's second part and then the whole file

**Interfaces:**
- Consumes: Task 11's wording; the finished prototype's `drConsole` members, fixtures, `HALTED_EVENTS`, `EVIDENCE`.
- Produces: DESIGN.md §6's stage-keyed summary, which Task 13's api-gaps.md mirrors (E1 first, live delivery under Running, SB rows).

- [ ] **Step 1: The failing scoped check**

```bash
awk 'f||/^## 4\. Status mapping/{f=1; print}' docs/design/DESIGN.md | rg -c -e 'fact_checker' -e '\bcritic\b' -e '\bCritic\b' -e '[Cc]ritique' -e 'synthesizer' -e 'destination:\s*"?refine' -e 'graph\.refinement' -e '\bRefinements?\b' -e 'refinement (pass|budget|loop|track|request|strip)' -e 'iteration \S+ of' -e '[Cc]orroboration' -e '[Cc]laim confidence'
```

Expected before: a positive count (the stale names in §4–§7).

- [ ] **Step 2: §4 (`:804-847`, from `## 4. Status mapping` through rule 6)**

```markdown
## 4. Status mapping

The API's `SessionStatus` is a five-value literal (`api/models.py:13-19`). The
interface shows five statuses. This table is the contract between them, and it is
exhaustive: no status may be invented and none may be dropped.

| Interface status | API `status` | Also read | Token role | Copy shown to the operator |
|---|---|---|---|---|
| **Running** | `running` | the pass from the stream (§3.5) | `--fg` label, `--success` live dot (the one non-text use) | `Running · pass p of P` |
| **Completed** | `completed` | `semantic_review_score`, `coverage.not_found_target_ids` | `--fg` label, `--success` dot | `Completed · review accepted · {score}` + the not-found clause |
| **Partially completed** | `max_iterations` | `coverage` | `--fg` label, `--warn` dot | `Partially completed · extra passes used` + the not-found clause |
| **Partially completed** | `incomplete` with `semantic_review_status == "scored"` | `semantic_review_score` | `--fg` label, `--warn` dot | `Partially completed · not accepted · {score}` |
| **Partially completed** | `incomplete` with any other `semantic_review_status` | — | `--fg` label, `--warn` dot | `Partially completed · review unavailable` |
| **Failed** | `failed` | `errors` | `--fg` label, `--danger` dot | `Failed · halted`; the failed stage headlines the halting type |
| **Unavailable** | *not a status* | any `null` field | `--muted` text, no chip, no icon, no control | `not measured`, `not scored`, `Not recorded`, `Not available while running` |

Rules that follow from the table:

1. **`failed` is not the same as "no report".** A halt sets `status="failed"` and
   records a non-recoverable `ResearchError`, but `GET /report` then answers
   `409 report_unavailable`, not 404. The failed screen states the failure and
   then states, separately, that nothing was published.
2. **`completed` is exactly `report_accepted`.** That route reason is the only one
   that yields `completed` (`graph/state.py:116`, `:322-337`), so the chip infers
   acceptance from the status alone and reads the score from
   `semantic_review_score`, printed with two decimals. There is no
   `quality_status` on the response and none is needed.
3. **`max_iterations` and `incomplete` are not degraded states.** They render with
   the same visual weight as `completed` — a different dot colour and an accurate
   clause, never a warning banner, never an apology. The clause is counted, never
   assumed: `n = coverage.not_found_target_ids.length` is omitted at 0, reads
   `· 1 target not found` at 1 and `· {n} targets not found` above 1
   (`max_iterations` can end with an empty list when only the review's own
   coverage defect remained, `state.py:312-313`). `not accepted · {score}` covers
   a passed review too: acceptance needs a scored review with a mean of at least
   0.80 and no material defect, complete coverage, and no quality-gate hard
   failure (`agents/report_reviewer.py:416-443`, `state.py:306-314`), so a gate
   can block acceptance while the review itself passed, and the clause says the
   report was not accepted rather than that the review failed. The report is
   present and authoritative, and it says itself what it could not confirm.
4. **`unavailable` never becomes a value.** `trace_url: null` is
   `Not available while running` in `--muted`; `report_path: null` is
   `Not published`; token usage is `Not recorded` (its source —
   `total_token_usage` — documents that zero means "no provider reported usage",
   so a summed `0` is not a fact about the run); `evidence_counts: null` is
   `not measured`; `semantic_review_score: null` is `not scored`. No `0`, `—`,
   `null`, empty chip, or greyed-out button stands in for a missing value.
   **A missing value is text, and it is always visible text.**
5. **Status is never carried by colour alone.** Every status has a text label at
   `--text-sm` minimum; the dot is redundant reinforcement, and a duplicated
   colour-vision simulation of the chips is in `states.html`.
6. **`waiting` does not exist.** Before the first frame, or between
   `POST /research` and the first event, the screen is *Running* with stage
   `starting`. There is no sixth status.
```

- [ ] **Step 3: Derived stage display (`:849-871`)**

```markdown
### Derived stage display

`current_agent` is only populated between `graph.node.started` and the node's
completion, and is `null` from the first `graph.node.completed` of the terminal
node until the response arrives. The stage spine therefore derives its state from
the event stream, not from `current_agent`:

| Node | Label | Row |
|---|---|---|
| `planner` | Planning | 1 |
| `researcher` | Researching | 2 |
| `source_evaluator` | Evaluating sources | 3 |
| `evidence_verifier` | Verifying evidence | 4 |
| `report_writer` | Writing report | 5 |
| `report_reviewer` | Reviewing | 6 |
| `extra_pass` | — | a hop, not a row: the graph returns to 2 |
| `writer_redraft` | — | a hop, not a row: the graph returns to 5 |
| `finalize_report` | Publishing | 7 |

`current_agent` is used only as a fallback when the log is empty. A row is `done`
on its own `graph.node.completed` — except Reviewing, whose completion is inert
after a loop decision (§3.5) — and `skipped` on its `graph.node.skipped`; the
active row is the successor of the last completion, with the loop keyed on
`graph.route.decided`. Publishing is marked `skipped` on a halted run by the
client rule `graph.session.completed.status == "failed"` — never by
`has_report`, which can be true when the halt came after a first-pass writer
(`agents/report_writer.py:3262`) — because the graph never runs
`finalize_report` after a halt and no event exists for it.
```

- [ ] **Step 4: `iteration` in two places (`:873-914`)** — keep the heading; replace the body through `in the first place.` with:

```markdown
The session's pass shows up in the topbar chip, the running header and the report
header. Three rules, all learned from bugs:

- **It is read from the event stream, not the snapshot — and only from the
  graph's own events.** While a run is live the pass number is `iteration + 1`
  from `graph.node.started` and `graph.extra_pass.started`, and from nothing
  else: `researcher.tool_call` also carries an `iteration`, but that is the ReAct
  step index (`agents/researcher.py:2612`), and a consumer that copied
  `iteration` from every event would show the pass jumping mid-research.
  `/status.iteration` is read only after the stream has closed. The topbar is
  otherwise only rebuilt on a stage change, and an earlier build rendered it once
  — so the chip read the first pass for an entire run while the pipeline moved
  on.
- **A finished run's pass is the pass it ended on, not the ceiling.** It is
  `last iteration carried by a graph event + 1`, so a one-pass run reports
  `pass 1 of 2`. Hardcoding the configured maximum made a single-pass run claim
  it had looped.
- **The ceiling beside it comes from the run, not from a constant.**
  `P = 1 + max_extra_passes`, read from `graph.session.started` while the stream
  is open and from the budget the session was created with otherwise; it is the
  same figure the pipeline's `pass p of P` reads, so the two cannot drift. A
  session carrying no ceiling drops the clause and shows `pass 1` alone, because
  a wrong ceiling is worse than an absent one.

Internal iteration is zero-based — the first pass is `iteration: 0`; an extra
pass advances it and a redraft does not. **The interface never shows that
value.** Every site that displays it adds one, through a single `passNumber()`
helper, so the operator sees the pass they are on rather than a zero-based index:

| Shows | Value | Notes |
|---|---|---|
| Topbar chip | `passNumber(iteration)` over the run's own ceiling | `pass 2 of 2` during the extra pass of a run created with one |
| Running header | the same, from the run state | stays `pass 1 of 2` while a redraft runs |
| Report header and Session facts | the pass it ended on | `pass 2 of 2` for `b41e77aa`, which spent its extra pass |

The stored session keeps the API's zero-based value. Converting at the display
boundary rather than in the data keeps the prototype's model faithful to the
server's, and keeps one helper as the only place the offset lives — an offset
applied by hand at each display site is how the chip and the report drifted apart
in the first place.
```

- [ ] **Step 5: Error rendering (`:916-959`)** — replace the whole section body (keep the heading) with:

```markdown
`ResearchError` carries `error_type`, `source`, `message`, `recoverable`,
`timestamp`, `details` (`utils/types.py:1345-1351`). Two levels only:

- `recoverable: true` — **not surfaced while a run is in progress.** A recoverable
  error — a statement-check batch the writer could not judge
  (`report_writer_statement_check_failed`), a context-check batch the verifier
  could not judge (`evidence_verifier_context_check_failed`), a section the
  writer could not draft — is the normal case: it never stops the run, and the
  evidence log records what it left unchecked. A live counter was therefore a
  number that asked to be read and told the operator nothing actionable, and the
  card that held it was removed before the rail itself was.
- `recoverable: false` — promoted into the failure panel, with the halting type
  **in plain words** as the headline, the API's own `message` as the sentence
  beneath it, and the enumerated `error_type`, `source`, `recoverable` and
  `report_path` as labelled rows. This is the one place an error is a headline,
  because it is the reason the run ended.

**`message` was being discarded, and that is now fixed.** `graph/errors.py` is explicit that
an error's `message` is curated per enumerated type and is never `str(exception)`. The failed
panel nonetheless carried a fixed sentence, and the rail's Errors card rendered `error_type`
alone — so on both surfaces where errors appear, the one field written as a sentence for a
human to read was the one field not shown. Both now render it, falling back to a static
sentence when an error record carries no message. The fixture data was corrected at the same
time: it had been carrying error types the API does not define, and no `message` on any error.
The fixtures now carry only real types — `report_writer_statement_check_failed`
(`agents/report_writer.py:2023`), `evidence_verifier_context_check_failed`
(`agents/evidence_verifier.py:1106`) and the halting `graph_provider_configuration_error` —
each with its curated message.

**`timestamp` and `details` are still served and still not rendered**, and this section used to
claim otherwise about `details`. It is a flat dict whose shape changes with the error type —
`{"exception_type": …}` for a provider failure, a batch index for a failed check, a part title
for a failed section — so rendering it means deciding how to present arbitrary structured data:
how many array items before truncating, what to do with a nested object, whether an empty dict
shows nothing or a dash. That is a presentation decision rather than an oversight, which is why
it is written down here instead of guessed at. `message` was the same class of gap with an
obvious answer, which is why it was fixed and this was not.

Halting types (`graph/state.py:141-150`) end the run; everything else is
recoverable and does not. The failed stage headlines each in plain words:

| `error_type` | Headline |
|---|---|
| `graph_planning_failed` | Planning failed |
| `graph_provider_configuration_error` | Model provider misconfigured |
| `graph_agent_configuration_error` | Agent misconfigured |
| `graph_invalid_agent_state` | Invalid agent state |
| `graph_invalid_route` | Invalid route |
| `graph_request_attempt_limit_exceeded` | Request attempt limit reached |

The report stage still carries an errors panel with a count and a disclosure, since
that is a finished run being reviewed rather than a live one being watched, and the
count there is answerable against the report it belongs to.
```

- [ ] **Step 6: §5.4 one sentence (`:1104-1107`)** — append to the paragraph ending `a grid track rather than a named width.`:

```markdown
The Evidence view reuses the same `--rail` track for its detail pane, and the
running stage has no rail at all — its counters sit inside the pipeline card — so
this revision added no custom property.
```

- [ ] **Step 7: §5.7, §5.8, §6 (`:1362-1412`)**

```markdown
### 5.7 Event stream is not a surface

The stream is consumed, not rendered. The running stage shows what it derives
from it — the active pipeline node (with its one-line explanation), the stage
position, `pass p of P`, the progress bar, the two arcs and the loop tag, and the
counters block — and nothing else. A raw log is deliberately absent from the main
region.

What each surface derives, and from which events (`graph/events.py`,
`agents/*.py`): the active row from `graph.node.completed` and
`graph.route.decided`; the pass from `graph.node.started` and
`graph.extra_pass.started`; the ceiling from `graph.session.started`; the arcs
and the loop tag from `graph.route.decided`, `graph.extra_pass.started` and
`graph.report.redraft_requested`; the counters from `planner.planning.completed`,
`researcher.sub_topic.completed`, `researcher.tool_call`,
`researcher.research.completed`, `source_evaluator.evaluation.completed`,
`evidence_verifier.verification.completed`, `report_writer.report.written` and
`graph.report.reviewed`; the failed stage's skipped rows from
`graph.node.skipped` and its Publishing row from `graph.session.completed`.

**Delivery is once per node step.** The orchestrator publishes each
`stream_mode="values"` snapshot's new events together
(`graph/orchestrator.py:312-346`), so a node's `graph.node.started`, everything
it emitted and its `graph.node.completed` arrive at once, when the node finishes.
The screen therefore moves once per node — and during the researcher, the longest
stage, the research counters read `not yet` for its whole duration. Every
derivation above is written so the state after event *k* depends only on events
1..*k*: a 100-event replay, a per-node burst and a one-per-tick playback paint
the same screen. Live per-event delivery is an API gap (api-gaps 3.7), listed for
the API work.

This is a product judgement, stated so it can be overruled: a run emits well over
100 events, the operator's question is "is it progressing and what has it found",
and a tail answers neither better than a stage spine with counters does. The
event stream stays the source of truth for the derived values, so nothing is lost
that a future log view could not reintroduce.

### 5.8 Counted from the event stream

The running stage carries a compact counters block inside the pipeline card,
under its header, with the eyebrow `counted from the event stream`. Its rows,
each with its scope:

| Row | Scope | From |
|---|---|---|
| sub-topics researched | this pass | `{count} this pass` while only `researcher.sub_topic.completed` events have arrived; then `{researched} of {researched + skipped}` from `researcher.research.completed` |
| tool calls | whole run · researcher only | the number of `researcher.tool_call` events |
| findings | this pass | `researcher.research.completed.findings` |
| sources scored | whole run | `source_evaluator.evaluation.completed.source_count` |
| verified / corrected / dropped | this pass | `evidence_verifier.verification.completed` |
| sentences / refused | current draft | `report_writer.report.written.statements` / `.refused` |
| review score | latest review | `graph.report.reviewed.mean_score`, two decimals; muted `not scored` when null |

Rules: a counter whose event has not arrived reads muted `not yet`, never `0`;
counters update once per node step, because that is how the stream delivers
events (§5.7); on `graph.extra_pass.started` the this-pass rows reset to
`not yet` and the block's caption reads `pass p`; on
`graph.report.redraft_requested` the current-draft rows and the review score
reset. The failed stage freezes the same rows at the halt, and a row whose node
never ran reads `not reached`.

The block is honest now where the earlier cost card was not: real values arrive
during the run, one node at a time, and each row names the pass or draft it
counts. Token usage is still `Not recorded`: totals are **not** in
`ResearchSessionResponse` — they live in `ResearchOutcome.token_usage`, computed
at the end of the run — and the report stage's "Cost and usage" card says so.
Nothing is ever rendered as `0`: `total_token_usage` already documents that a
zero total means "no provider reported usage", so a rendered `0` would assert
something the API never said, and the stream-derived, researcher-only tool-call
count is not copied into that card.

---

## 6. What each stage needs that the API does not serve

Full detail, with the request shape each gap implies, is in
[`api-gaps.md`](./api-gaps.md). Summary, keyed to the five stages of §3:

| Stage | Blocked by |
|---|---|
| Evidence (every stage) | **E1** — no `GET /research/{id}/evidence`: the Evidence view, the `Download evidence log` button and coverage's question text are prototype-only until it exists |
| Idle | the session's own `query` is never returned; no endpoint lists sessions; no effective-settings echo; no `/capabilities`; no `/health` |
| Submitted | nothing beyond Idle |
| Running | events arrive once per node step, not live per event; `max_extra_passes` is on the stream but not on `/status`; no token usage; no terminal frame; no event identity for reconnects; the halting vocabulary is a client copy; shutdown leaves `running` |
| Report | Markdown only (a JSON projection is a nice-to-have now that the format is stable); no report hash on the response |
| Failed | what survived a halt comes only from the stream; the halted state still needs a seeded session |
| Sidebar | no `GET /research`; no result summary per row; no durable store |

Every one of these is worked around in the prototype rather than faked: the gaps
document names the workaround and, where there is no honest workaround, the
prototype says so in place instead of rendering a zero.
```

- [ ] **Step 8: §7 (`:1414-1471`)** — replace the paragraph beginning `For review, \`window.drConsole\` exposes` and everything after it with:

```markdown
For review, `window.drConsole` exposes `submit(question)`, `open(sessionId)`,
`finish()`, `stage()`, `motion()` and the `sessions` ledger, plus — since
2026-09-26 — `view(name?)` (switches or reports the `Report | Evidence` toggle),
`evidence(sessionId)` (opens a session on its Evidence view),
`advanceTo(eventType)` (plays the active playback session's script synchronously
up to and including the first event of that type, then pauses), `arc()`
(`"extra_pass"`, `"redraft"` or `null`) and `loop()` (`"off"`, `"flowing"` or
`"settled"`). Any stage and either arc can be reached directly without watching a
run. Jumping to a stage through the hook uses the plain stage transition rather
than a handoff, which is the correct behaviour and also the quickest way to see
the two side by side: toggle `prefers-reduced-motion` and repeat either handoff
to confirm that no state information lives in the motion.

**The fixture sessions** are shaped like `ResearchSessionResponse`, ids reused
from the 2026-09-16 package and every question from the battery-storage set:
`8f2c1d90` (playback; a pass-0 review names two missing targets → extra pass →
accepted at 0.86 on pass 1), `c3d7e5f1` (playback; a pass-0 review names one
material defect → redraft → re-review accepts at 0.84), `b41e77aa` (`completed`
after its extra pass was spent, one target not found — the shape of the replay
case `extra-pass-finds-nothing`), `7c0d13ff` (`max_iterations`, one target not
found), `5ff1ab07` (`incomplete`, not accepted at 0.71, budget 0), `9ea4c220`
(`incomplete`, review unavailable; its `Scored sources cited` is 4 of 5 = 0.80,
painted yellow) and `2ad900b1` (`failed`, `graph_provider_configuration_error` in
the planner). The halted fixture carries a static `HALTED_EVENTS` list —
`graph.session.started`, `graph.node.started` for the planner, five
`graph.node.skipped`, `graph.session.completed` with `status: "failed"` —
consumed once by the failed-stage renderer through the same event handlers the
running stage uses, never played. The two playback fixtures share one `play`
state: opening one restarts its script from the beginning, and the other keeps
`status: "running"` until it is reopened.

**The Evidence fixture** (`EVIDENCE.default`) is one set in the shape api-gaps
E1 proposes, for the one battery-storage report: six findings covering every
status (including one with no verification, shown only under `All`), one
not-found target (`T04`) with its queries and pages read, and one refused
sentence citing `F03`. Every value is one the engine can produce — figure
attribution `own`/`relayed`, kind `actual`, no figures on a quoted or dropped
finding, and the `context unchecked` flag on a verified finding with a kept figure
(`F06`). `F03`'s source scores 0.80 overall, so its `overall` meter paints yellow
(§3.6).

`prototype/states.html` is the review companion from the previous pass. It stays
useful as the rendering contract for the states the console reaches only in
unusual circumstances — empty, configuration error, session failed, the three
partial outcomes, an options table wide enough to scroll — and it carries the
status-mapping table rendered as live chips, so §4 can be read against the
actual pixels. It is not part of the app.

Both files are self-contained: no build step, no external scripts, no network
fetches, no web fonts. All colour, type, space, radius and motion values resolve
through the design system's custom properties; the only literal colours in either
file are inside the single `:root` block. The sidebar's collapsed/expanded choice
and the last-opened session persist to `localStorage`
(`dr.console.sidebar`, `dr.console.active`), and the page uses `window.scrollTo`
rather than `scrollIntoView`.

**Three working behaviours are scripted rather than wired, and the UI no longer says so about
all three.** The run is driven by an event sequence whose names and metadata keys are the
engine's own (checked offline against two replayed runs, spec §4.6) rather than a live
`EventSource`; the sidebar is a client ledger because there is no collection route (§3.1); and the
report body is one real published Markdown document, rendered into the consumer format, reused
for every completed session.

That last one used to be labelled inside the report card — a note explaining that the body is a
fixture and that the app replaces it with the body returned from `GET /research/{id}/report`.
The operator asked for the note to go, and it has. **The limitation did not go with it:** what the
report stage shows is the same document for every session, and nothing on screen says so now. It
remains recorded here and in `api-gaps.md`, and it is the one thing in this file that should
probably be disclosed on screen again — briefly — wherever the prototype is shown to someone who
did not build it.
```

- [ ] **Step 9: Run the checks**

```bash
node "$TEMP/dr-probe/frozen.mjs"
rg -n -e 'fact_checker' -e '\bcritic\b' -e '\bCritic\b' -e '[Cc]ritique' -e 'synthesizer' -e 'destination:\s*"?refine' -e 'graph\.refinement' -e '\bRefinements?\b' -e 'refinement (pass|budget|loop|track|request|strip)' -e 'iteration \S+ of' -e '[Cc]orroboration' -e '[Cc]laim confidence' docs/design/DESIGN.md
rg -n -e '^### 5\.8 Counted from the event stream' -e '^\| Evidence \(every stage\) \|' -e 'advanceTo\(eventType\)' docs/design/DESIGN.md
```

Expected: `frozen.mjs` — nine `OK` lines, exit 0; the stale-name `rg -n` over the whole file prints nothing (was 39 lines); the three markers each print one line.

- [ ] **Step 10: Commit**

```bash
git add docs/design/DESIGN.md
git commit -m "docs(design): DESIGN.md §4–§7 — statuses and clauses, derived rows, pass number, halting headlines, the counters block, fixtures and hooks"
```

---

### Task 13: `api-gaps.md` rewrite and `README.md` (spec §4.5, §4.6 README row, AC16)

**Files:**
- Rewrite: `docs/design/api-gaps.md` (whole file)
- Modify: `docs/design/README.md:6-11` (Provenance), `:19` (the `prototype/index.html` row), `:28-31` (`open-design/` intro), `:64-71` (delete "Known defect in this package")
- Test: `$TEMP/dr-probe/t13-docs.mjs` (Node — never `bash <script>`)

**Interfaces:**
- Consumes: the finished `index.html` (its line count is measured here), DESIGN.md §3 stage numbering and §6 (Tasks 11–12).
- Produces: gap ids `E1`, `1.1–1.5`, `2.—`, `3.1–3.7`, `4.1–4.2`, `5.1–5.2`, `SB.1–SB.2` that DESIGN.md §6 and the spec reference.

- [ ] **Step 1: Write the failing check**

```bash
cat > "$TEMP/dr-probe/t13-docs.mjs" <<'EOF'
// Throwaway: AC16 (api-gaps.md structure) and the README rows this plan changes.
import { readFileSync } from 'node:fs';
const norm = (s) => s.replace(/\r\n/g, '\n');
const G = norm(readFileSync('docs/design/api-gaps.md', 'utf8'));
const R = norm(readFileSync('docs/design/README.md', 'utf8'));
const P = readFileSync('docs/design/prototype/index.html', 'utf8');
let bad = 0;
const check = (name, cond, detail = '') => { console.log(`${cond ? 'OK  ' : 'FAIL'} ${name}${cond ? '' : ' — ' + detail}`); if (!cond) bad++; };
const heads = [...G.matchAll(/^## (Stage [1-5] — (?:Idle|Submitted|Running|Report|Failed)|Sidebar)\b/gm)].map((m) => m[1]);
check('five stage sections + sidebar, in DESIGN.md §3 order',
  JSON.stringify(heads) === JSON.stringify(['Stage 1 — Idle', 'Stage 2 — Submitted', 'Stage 3 — Running', 'Stage 4 — Report', 'Stage 5 — Failed', 'Sidebar']), JSON.stringify(heads));
const e1 = G.indexOf('\n## E1 — '), s1 = G.indexOf('\n## Stage 1 — ');
check('E1 is the first gap and carries the JSON shape and ?format=markdown', e1 > 0 && s1 > e1 && G.includes('"not_found": [') && G.includes('?format=markdown'), `e1=${e1} s1=${s1}`);
const closedSection = (G.split('\n## Closed since 2026-09-16')[1] || '').split('\n## ')[0];
const closedRows = (closedSection.match(/^\| (2\.4|3\.2|3\.3|3\.5|2\.2) \|/gm) || []).length;
check('five closed items recorded (counted inside the Closed section only)', closedRows === 5, String(closedRows));
check('gap 3.7 live per-event delivery', /^\| 3\.7 \| \*\*Live per-event delivery/m.test(G));
check('4.1 downgraded to nice-to-have', /^\| 4\.1 \|.*nice-to-have/m.test(G));
check('SB.1 and SB.2', /^\| SB\.1 \|/m.test(G) && /^\| SB\.2 \|/m.test(G));
const bullets = [...G.matchAll(/^- \*\*([^*]+)\*\*/gm)].map((m) => m[1]);
check('should-not-close list ends with per-agent effort editing', bullets[bullets.length - 1] === 'Per-agent effort editing.', JSON.stringify(bullets.slice(-2)));
check('route table lists five routes and 17 fields', (G.match(/^\| `(?:POST|GET)` \|/gm) || []).length === 5 && G.includes('17 fields'));
const flat = G.replace(/\*\*/g, '').replace(/\s+/g, ' ');
check('governing rule verbatim', flat.includes('where a value is unavailable, the interface says so in muted text. It never renders `0`, `—`, `null`, a placeholder, or a disabled control.'));
check('README has no Known defect section', !/Known defect/.test(R));
check('README revision note', R.includes('Updated 2026-09-26 to the Evidence Verifier pipeline (f27ac7e)'));
let lines = (P.match(/\n/g) || []).length; if (!P.endsWith('\n')) lines += 1;   // count lines as an editor does
const pretty = lines.toLocaleString('en-US');
check(`README line count ${pretty}`, R.includes(`${pretty} lines`), `README must say "${pretty} lines"`);
const plines = P.split(/\r?\n/);
const i = plines.findIndex((l) => l.startsWith(':root{')); let j = i + 1; while (j < plines.length && plines[j] !== '}') j++;
const a = i + 1, b = j + 1;
check(`README token-line range ${a}–${b}`, R.includes(`lines ${a}–${b}`), `README must say "lines ${a}–${b}"`);
console.log(bad ? `${bad} failure(s)` : 'OK');
process.exit(bad ? 1 : 0);
EOF
node "$TEMP/dr-probe/t13-docs.mjs"
```

Expected before: exit 1 — 11 of 13 lines `FAIL` (today's file has `## Stage 1 — Idle (composer)` … `## Stage 4 — Failed` and no E1) and on three of the four README lines (the "Known defect" section exists; no revision note; the row says `lines 12–45`). Two checks already pass today and stay green: the governing-rule sentence (kept verbatim) and the README line count (`3,067 lines` is correct until a later task edits `index.html`).

- [ ] **Step 2: Rewrite `docs/design/api-gaps.md` (whole file)**

````markdown
# Per-stage API gaps

What each stage of the console needs that the current FastAPI surface does not
serve. Every entry names the ground truth — the object that already holds the
value inside the process — so the gap is a transport gap rather than missing data.

The rule this document exists to protect: **where a value is unavailable, the
interface says so in muted text. It never renders `0`, `—`, `null`, a placeholder,
or a disabled control.**

The console is one page with five stages (DESIGN.md §3: 1 Idle, 2 Submitted,
3 Running, 4 Report, 5 Failed) and a collapsible session sidebar, so gaps are
keyed `{stage}.{n}` plus `SB.{n}` for the sidebar, with `E1` — the one new
endpoint every stage would use — listed first. Re-keyed on 2026-09-26 to the
Evidence Verifier pipeline (`f27ac7e`); the 2026-09-16 ids are kept in brackets.

---

## Existing surface, for reference

| Method | Path | Returns |
|---|---|---|
| `POST` | `/research` | `202` `ResearchSessionResponse` (`api/app.py:159-188`; `max_iterations` is passed to the graph as `max_extra_passes`, `:183`) |
| `GET` | `/research/{id}/status` | `200` `ResearchSessionResponse` (`:190-202`) |
| `GET` | `/research/{id}/stream` | `200` `text/event-stream`, replayed from id 1 then live; ids restart at 1 per subscriber (`:204-236`, `api/events.py:14-27`) |
| `GET` | `/research/{id}/report` | `200` `text/markdown`, or `409` `session_not_complete` / `report_unavailable` (`:238-263`) |
| `GET` | `/research/{id}/trace` | `200` `TraceResponse` (`:265-269`) |

`ResearchSessionResponse` (`api/models.py:114-164`, assembled at
`api/sessions.py:73-128`), 17 fields: `session_id`, `status`, `current_agent`,
`iteration`, `started_at`, `finished_at`, `report_path`, `trace_url`, `errors`,
`evidence_path`, `quality_path`, `quality_contract_version`,
`semantic_review_status`, `semantic_review_score`, `duration_seconds`,
`coverage` (`required_targets`, `answered_targets`,
`missing_required_target_ids`, `not_found_target_ids`) and `evidence_counts`
(fifteen counts; `null` when the run left neither a composition nor a quality
snapshot, `runtime/outcome.py:525`). `status` is one of `running`, `completed`,
`max_iterations`, `incomplete`, `failed`. Not on the response: `quality_status`,
`query`, `max_iterations`/`max_extra_passes`, token usage, tool-call totals, any
report structure.

`ResearchRequest` accepts `query`, `max_iterations` (`int | None`, `ge=0`,
default → config), `output_format` and `config_overrides`; overrides are
validated against `ConfigSettings()`, so an unknown path is a `422` before a
session exists.

**Delivery model.** Events reach the stream once per node step: each
`stream_mode="values"` snapshot publishes the events that superstep appended
(`graph/orchestrator.py:312-346`), so a node's `graph.node.started`, everything
it emitted and its `graph.node.completed` arrive together when the node finishes.
Every running-stage rule in DESIGN.md §3.5 and §5.7 is written for that.

---

## Closed since 2026-09-16

Kept as a record, one line each.

| Old # | Gap | How it closed |
|---|---|---|
| 2.4 | `quality_status` | `completed` ⇔ route `report_accepted` (`graph/state.py:116`, `:322-337`); `semantic_review_status` splits the partial outcomes |
| 3.2 | quality snapshot | `coverage`, `evidence_counts`, `semantic_review_status`, `semantic_review_score` are on the response (`api/sessions.py:73-128`) |
| 3.3 | `evidence_path` | the path is served; the content moves to E1 |
| 3.5 | claim verdicts and confidence | obsolete: the pipeline has no claims; findings carry a verification status instead (E1) |
| 2.2 | tool-call counts | mostly closed: the verifier, writer and reviewer make no tool calls; `researcher.tool_call` misses only the planner's single budgeted call (`config.yaml:159`; `agents/planner.py:2999`), whose count is on `planner.planning.completed.tool_calls` |

---

## E1 — `GET /research/{id}/evidence`

The one endpoint every stage would use. JSON by default; `?format=markdown`
returns the published evidence log as `text/markdown`, which is what makes a
`Download evidence log` button appear on the report stage (today the button is
absent, never disabled). Subsumes the old 3.4 (citations with URLs), 3.6 (source
scores and bands) and the coverage id → question need. Ground truth:
`ReportComposition` and `ResearchState`.

| Needed | Why | Where it exists today | Honest workaround | Suggested shape |
|---|---|---|---|---|
| **The findings, their verification and their sources** | The Evidence view (DESIGN.md §2 C) lists every finding with its status, target, snippet, source scores, Context Check and figures; coverage's not-found ids want their question text; the report's `How this was researched` link wants a destination | `composition.finding_labels`, `FindingVerification` (`utils/types.py:413-419`), `ScoredSource` (`:625-640`), `FigureResult` + `FigureContext` (`:368-400`), `NotFoundTarget` (`:1521-1532`), `RejectedDraftPoint` (`:1476-1486`), `composition.statement_passages` | The real app shows one muted line, `not served by the service yet`, where the list would be; the prototype renders the target state from a fixture in this shape | the JSON below |

```json
{"session_id": "…", "iteration": 0,
 "findings": [{"label": "F06", "status": "verified", "dropped_reason": null, "context_unchecked": false,
               "cited": true, "target_ids": ["T01"], "content": "…", "snippet": "…", "passage": null,
               "source": {"url": "…", "title": "…", "organisation": "…", "evaluation_status": "scored", "low_confidence": false,
                          "authority_score": 0.8, "recency_score": 0.7, "relevance_score": 0.9, "overall_score": 0.8},
               "figures": [{"value": "10.4 GW", "kept": true, "period": "2024", "scope": "…", "organisation": "…",
                            "attribution": "own", "kind": "actual", "release": "…", "evidence_words": "…",
                            "corrected": false, "dropped_reason": null, "reason": null}]}],
 "not_found": [{"target_id": "T02", "question": "…", "queries": ["…"], "pages_read": ["…"], "searched": true}],
 "refused": [{"where": "…", "text": "…", "reason": "…", "finding_labels": ["F03"]}]}
```

| Field group | Source type |
|---|---|
| finding `label`, `status`, `dropped_reason`, `context_unchecked` | `composition.finding_labels` (`types.py:1658`); `FindingVerification` (`types.py:413-419`) |
| `passage` | `composition.statement_passages` (`agents/report.py:1940`) |
| `source` scores and statuses | `ScoredSource` (`types.py:625-640`); `organisation` = the Context Check's organisation or the page owner |
| `figures[]` | `FigureResult` + `FigureContext` (`types.py:368-400`): `attribution` ∈ own \| relayed \| unattributed, `kind` ∈ actual \| forecast; `release` as the evidence log prints it (`report.py:1961-1962`); a quoted or dropped finding carries none |
| `not_found[]` | `NotFoundTarget` (`types.py:1521-1532`) |
| `refused[]` | `RejectedDraftPoint` (`types.py:1476-1486`) |
| `cited` | what the response's `evidence_counts.cited_findings` sums |

---

## Stage 1 — Idle (composer)

| # | Needed | Why | Where it exists today | Honest workaround | Suggested shape |
|---|---|---|---|---|---|
| 1.1 | **The session's own `query`** [1.1] | Neither `POST /research` nor `GET /status` echoes the question back. The sidebar row, the running header and the report header all display it, so all three would be empty after a reload. | `ResearchSession.query`, `ResearchState.original_question` | The client keeps its own copy at submit time, and the sidebar states that it is client-assembled | Add `query: str` to `ResearchSessionResponse` |
| 1.2 | **The session list `GET /research`** [1.2, 5.1] | There is no collection route, and `new_session_id()` mints the id server-side, so a client cannot even enumerate what it cannot already name. The sidebar is this gap's whole surface. | `SessionStore._sessions` | Client ledger of ids from `202` responses, disclosed in the sidebar footer | `GET /research?limit=` |
| 1.3 | **The effective settings echo, now including per-role effort** [1.3] | The composer sends `llm.model` and `llm.thinking_mode` as overrides and `max_iterations` at the top level, then cannot show what was actually used; effort is fixed per role in `llm.model_overrides` (`config.yaml:32-63`) and the response has no config block, so the settings strip shows the submitted values and the effort line states the configured ones. | `ConfigSettings.llm` after `apply_config_overrides` | Show the submitted values, labelled as submitted; state effort per agent from the configuration | A non-secret `config` block: `provider`, `model`, `thinking_mode`, per-role `reasoning_effort`, `max_extra_passes` |
| 1.4 | **`GET /capabilities`** [1.4 + 1.5] | The provider override is now accepted by validation, but nothing lists the valid provider/model/thinking-mode/effort combinations, so the composer mirrors `capabilities.py` by hand (three DeepSeek models, `enabled`/`disabled`) and that copy drifts the moment the registry changes. | `_CAPABILITIES` in `providers/capabilities.py:71-77` | Hand-mirrored; the configured provider only | `GET /capabilities` returning `{provider, models[], thinking_modes[], enabled_efforts[]}[]` |
| 1.5 | **`GET /health`** [1.6] | A configuration failure is only discoverable by submitting, so the operator loses the question they just typed. | `prepare_research_settings` is already a side-effect-free callable | None; the compose surface cannot warn ahead of time | `GET /health` → `{ready: bool, reasons: [enumerated]}`, reusing the `configuration_error` reasons |

---

## Stage 2 — Submitted

| # | Needed | Why | Where it exists today | Honest workaround | Suggested shape |
|---|---|---|---|---|---|
| 2.— | Nothing beyond stage 1 | The submitted beat shows the question read back and the settings strip, both from the client's own copy (1.1, 1.3). It needs no field the idle stage does not. | — | — | — |

---

## Stage 3 — Running

| # | Needed | Why | Where it exists today | Honest workaround | Suggested shape |
|---|---|---|---|---|---|
| 3.1 | **`max_iterations` echo, partially closed** [1.7] | The ceiling `P` in `pass p of P` comes from `graph.session.started.max_extra_passes` while the stream is open, but `/status` never carries it, so a reload after the stream closes falls back to the client's submitted budget. | `ResearchState.max_extra_passes`; on the stream at `graph.session.started` | Read it from the stream; fall back to the submitted value, used consistently | Include `max_extra_passes` on the response |
| 3.2 | **Token usage, absent on every stage** [2.1] | "Cost and usage" wants token totals and the client cannot derive them; they are absent while running and after the run alike. | `ResearchOutcome.token_usage`, from `TokenUsageMetric`s accumulated in the tracker | `Not recorded`, with the reason stated | A `usage` block on the status response, or cumulative totals on `graph.node.completed` metadata |
| 3.3 | **A terminal frame on the stream** [2.3] | The stream ends when the session reaches a terminal state, but the final frame is an ordinary event. The console learns *that* the run ended and must then call `GET /status` to learn *how* — and the stage transition depends on knowing how. | `graph.session.completed` carries `status`, `iteration`, `error_count`, `has_report`, but the store returns without synthesising a frame | On stream close, re-read `/status` and transition from it | One terminal `api.session.closed` frame carrying the final snapshot |
| 3.4 | **Event identity / `Last-Event-ID`** [2.6] | SSE `id` is per-subscriber and starts at 1, so it is a stream position rather than an event identity. A reconnect cannot ask for "everything after what I saw". | `ResearchSession.events` list index | Re-derive the running stage from the full replay — every rule is idempotent over events 1..k | A monotonic `sequence` on `ResearchEvent`, plus `Last-Event-ID` support |
| 3.5 | **Halting-type vocabulary** [2.5] | The failed stage headlines the halting type in plain words and the rail groups recoverable errors, but the client keeps its own copy of `HALTING_ERROR_TYPES` to know which is which. | `HALTING_ERROR_TYPES` in `graph/state.py:141-150` | Client copy, small and stable | `halting: bool` on `ResearchError`, or publish the enumerated set |
| 3.6 | **Shutdown while running** [2.7] | On cancellation the store sets `finished_at` and leaves `status` as `running`, then the stream ends. The console sees a closed stream with a non-terminal status. | Deliberate: *"cancellation stays cancellation"* | On stream close, re-read `/status`; a closed stream with `finished_at` set and `status == "running"` means the service stopped | The terminal frame from 3.3, or an explicit status |
| 3.7 | **Live per-event delivery** (new) | Events are published once per node step (`graph/orchestrator.py:312-346`), not as they happen, so the running stage's counters and active row move once per node; during the researcher — the longest stage — the research counters read `not yet` for its whole duration. | The events exist as they are appended to `ResearchState.events`; only publication is batched per superstep | The design's burst-safe rules (DESIGN.md §3.5, §5.7): the state after event *k* depends only on events 1..*k*, so bursts, ticks and replays paint the same screen | Publish each event as it is appended (stream the node's events, not the superstep snapshot); listed for the API work |

---

## Stage 4 — Report

| # | Needed | Why | Where it exists today | Honest workaround | Suggested shape |
|---|---|---|---|---|---|
| 4.1 | **Report body JSON** [3.1], **downgraded to nice-to-have** | `GET /report` returns Markdown only. The consumer format is stable and parseable — H2 sections in a fixed order, source lines matching `^(\d+)\. ` (`e2e_evaluation/runner.py:55`) — so the console renders it as-is with presentation-only adaptations (DESIGN.md §2 A) and a JSON projection would merely save the parse. | `ReportComposition` | Render the Markdown as-is; the server owns wording, order, numbering and the table | `GET /research/{id}/report?format=json` returning a safe projection, if ever wanted |
| 4.2 | **Report hash on the response** [3.7] | The console cannot show that the body it fetched is the body that was judged, rather than an earlier pass's. `quality.json` already hashes both Markdown documents (`agents/report.py:347-357`, `:634`); the response does not expose them. | `render_quality_record` writes `"artifacts": {name: sha256}` | The console fetches once and caches | `artifacts: {report: sha256, evidence: sha256}` on the response, copied from the quality record |

---

## Stage 5 — Failed

| # | Needed | Why | Where it exists today | Honest workaround | Suggested shape |
|---|---|---|---|---|---|
| 5.1 | **What survived the halt** [4.1] | The stage shows the running stage's counters frozen at the halt. None of those counts are on the response — `evidence_counts` is `null` after a halt before the writer — so a reload has nothing to show. | `ResearchState` (findings, sources, verifications) at the halt | The stream counters, re-derived from the replay; a counter whose node never ran reads `not reached` | A `collected` summary block on the response, or `evidence_counts` computed from state even without a composition |
| 5.2 | **Reachable states** [4.2/4.3] | The two partial outcomes are now reproducible for free through the offline replay cases `review-unavailable` and `empty-but-clean`; the halted and configuration-error states still need a genuinely misconfigured service or a real failure. | The replay harness (`e2e_evaluation/replay.py`) for the partial states; nothing for a halt | Documented in `states.html` with the exact response body and the eight-frame event tail | A test-only seeded session, or the `GET /health` from 1.5 |

---

## Sidebar

| # | Needed | Why | Where it exists today | Honest workaround | Suggested shape |
|---|---|---|---|---|---|
| SB.1 | **A result summary per row** [5.2] | Rows want the terminal outcome and its clause (`review accepted · 0.86`, `1 target not found`). A row currently carries the question and a running mark and nothing else, so **the list cannot report how anything ended** — completed, partial and failed rows are indistinguishable until opened. | `semantic_review_status`, `semantic_review_score`, `coverage`, the route reason | The row carries only `status === "running"`; every settled outcome is one click away on the session itself | Fold the review, coverage and route reason into the list projection (1.2). This is the one gap whose absence is visible as a design decision rather than as missing text |
| SB.2 | **Durability** [5.3] | Sessions are process-local; the list resets when the API restarts. | By design — *"durable queues, databases … remain out of scope"* | Stated on screen. The console never implies persistence it does not have | A JSON-lines session index beside `output/` would be a smaller change than a database |

---

## Gaps the front end should *not* close

Recorded so nobody adds them later:

- **Collaboration, sharing, orgs, billing.** Single local operator, no auth. There
  is no identity to attach any of them to.
- **Re-run / cancel endpoints.** `SessionStore` cancels only on shutdown. A cancel
  route would need task ownership and a partial-artifact question the API has not
  answered; until it does, the console offers new research instead.
- **An embedded trace viewer.** `trace_url` leaves the application. LangSmith owns
  that surface and duplicating it would be a second, worse implementation.
- **A rendered event log.** The stream is consumed as derived counters, not
  rendered. That is a product decision (DESIGN.md §5.7), and reintroducing a log
  view needs no new endpoint, so it is not a gap.
- **Any count rendered as `0` when the source is absent.** Every unavailable field
  is muted text. This is the rule the rest of the document exists to serve.
- **Per-agent effort editing.** An override of `llm.model_overrides.<role>`
  replaces that role's whole entry and drops its `timeout`
  (`utils/config.py:664-686`); a global `llm.reasoning_effort` is shadowed by
  every role's own value (`config.py:85-107`). The composer states the configured
  effort per agent and offers no control for it.
````

- [ ] **Step 3: Edit `README.md`**

After the Provenance paragraph (`:8-11`, ending `nothing from it is omitted.`) add:

```markdown
*Updated 2026-09-26 to the Evidence Verifier pipeline (f27ac7e); the 2026-09-16
design is otherwise unchanged.* `open-design/` is the 2026-09-16 history and is
now older than the prototype: where it disagrees with the authoritative
artifacts, they win.
```

Measure the prototype the way an editor counts lines (`wc -l`, plus one when the last byte is not a newline — today the file has 3,066 newlines and ends in `>`, so it counts 3,067) and the token block's range:

```bash
n=$(wc -l < docs/design/prototype/index.html); [ "$(tail -c 1 docs/design/prototype/index.html | xxd -p)" = "0a" ] || n=$((n+1)); echo "lines: $n"
a=$(rg -n '^:root\{' docs/design/prototype/index.html | cut -d: -f1); b=$(awk -v s="$a" 'NR>s && /^}/ {print NR; exit}' docs/design/prototype/index.html); echo "root block: $a–$b"
```

Expected: `root block: 11–55` (unchanged by construction); `lines:` whatever the edit produced — write that number, with a thousands comma, into the `prototype/index.html` row (`:19`):

```markdown
| `prototype/index.html` | The reference prototype. Self-contained: N,NNN lines, one `<style>` block, one `<script>` block, zero external references. The only literal colour values live in the `:root` token block (lines 11–55), which is where the Perplexity AI design system is captured |
```

Replace `:28-31` (the `open-design/` intro) with:

```markdown
## `open-design/` — the complete original project

Everything here is a faithful copy for provenance: the 2026-09-16 design as it
was exported, now older than the prototype. Where it disagrees with the table
above, the table wins.
```

Delete the section `## Known defect in this package` (`:64-71`) entirely — the api-gaps rewrite re-keyed the gaps to DESIGN.md §3's five stages, which is what that section asked for.

- [ ] **Step 4: Run the check**

Run: `node "$TEMP/dr-probe/t13-docs.mjs"` → every line `OK`, then `OK`, exit 0.

Run: `rg -n -e 'fact_checker' -e '\bcritic\b' -e '\bCritic\b' -e '[Cc]ritique' -e 'synthesizer' -e 'destination:\s*"?refine' -e 'graph\.refinement' -e '\bRefinements?\b' -e 'refinement (pass|budget|loop|track|request|strip)' -e 'iteration \S+ of' -e '[Cc]orroboration' -e '[Cc]laim confidence' docs/design/api-gaps.md docs/design/README.md` → prints nothing (was 2 + 0).

- [ ] **Step 5: Commit**

```bash
git add docs/design/api-gaps.md docs/design/README.md
git commit -m "docs(design): api-gaps re-keyed to five stages plus the sidebar, E1 first, live delivery listed; README revision note and re-measured prototype"
```

---

### Task 14: Full verification AC1–AC18, regenerated renders, cleanup (spec §4.6 steps 1–4, §6)

**Files:**
- Regenerate if changed: `docs/design/reference/*.png`
- Delete: `$TEMP/dr-probe/` (outside the tree; nothing throwaway is ever in the tree)

**Interfaces:**
- Consumes: everything above.
- Produces: the summary the implementer reports (results of every check, the Chrome version, the render line), nothing else.

- [ ] **Step 1: AC1 — the stale-name command over the five authoritative files**

```bash
rg -n -e 'fact_checker' -e '\bcritic\b' -e '\bCritic\b' -e '[Cc]ritique' -e 'synthesizer' \
   -e 'destination:\s*"?refine' -e 'graph\.refinement' -e '\bRefinements?\b' \
   -e 'refinement (pass|budget|loop|track|request|strip)' -e 'iteration \S+ of' \
   -e '[Cc]orroboration' -e '[Cc]laim confidence' \
   docs/design/DESIGN.md docs/design/api-gaps.md docs/design/README.md \
   docs/design/prototype/index.html docs/design/prototype/states.html; echo "exit=$?"
```

Expected: no output lines, then `exit=1` (ripgrep's "no matches" code). Anything printed is a defect in the task that owns that file; fix it there and re-run.

- [ ] **Step 2: AC2, AC4–AC15 — the probe suite (every check exits 0)**

```bash
for t in t1-chips t1-clause t2-composer t2-zero t3-stages t4-rows t4-burst t4-switch t4-phone t5-extra t5-redraft t5-zero t5-burst t5-switch t6-failed t7-report t7-widths t7-finish t7-null t8-evidence t8-keys; do
  node "$TEMP/dr-probe/probe.mjs" docs/design/prototype index.html "$TEMP/dr-probe/$t.js" > "$TEMP/dr-probe/$t.out" 2>&1 && echo "OK   $t" || { echo "FAIL $t"; cat "$TEMP/dr-probe/$t.out"; }
done
node "$TEMP/dr-probe/probe.mjs" docs/design/prototype index.html "$TEMP/dr-probe/t4-phone.js" --viewport=390x844 > /dev/null && echo "OK   t4-phone (390x844)" || echo "FAIL t4-phone (390x844)"
node "$TEMP/dr-probe/probe.mjs" docs/design/prototype index.html "$TEMP/dr-probe/t5-reduced.js" --reduced-motion > /dev/null && echo "OK   t5-reduced" || echo "FAIL t5-reduced"
node "$TEMP/dr-probe/probe.mjs" docs/design/prototype index.html "$TEMP/dr-probe/t7-widths.js" --viewport=390x844 > /dev/null && echo "OK   t7-widths (390x844)" || echo "FAIL t7-widths (390x844)"
node "$TEMP/dr-probe/probe.mjs" docs/design/prototype states.html "$TEMP/dr-probe/t9-states.js" > /dev/null && echo "OK   t9-states" || echo "FAIL t9-states"
ls "$TEMP/dr-probe/profiles"
```

Expected: 25 `OK` lines, no `FAIL`; `profiles/` is empty (every probe removed its own Chrome profile).

- [ ] **Step 3: AC3 — the offline replay key check (spec §4.6 step 1), end to end**

```bash
PYTHONPATH=src PYTHONDONTWRITEBYTECODE=1 "C:/Users/Rahul Krishnamoorthy/OneDrive/Documents/Python Scripts/deep-research/.venv/Scripts/python.exe" "$TEMP/dr-probe/replay_keys.py" "$TEMP/dr-probe/captured.json"
node "$TEMP/dr-probe/check_keys.mjs" docs/design/prototype/index.html "$TEMP/dr-probe/captured.json"
git status --short   # must print nothing: no throwaway file is in the tree, no stray output
```

Expected: `captured 21 event types from {… 72, … 49}`, then `checked 20 emitted event types, 100 emitted keys + handler reads` and `OK`; `git status --short` empty.

- [ ] **Step 4: AC16 — the docs check; AC17/AC18 — byte identity**

```bash
node "$TEMP/dr-probe/t13-docs.mjs"
node "$TEMP/dr-probe/frozen.mjs"
git diff --quiet 2010c1d -- docs/design/open-design && echo "OK   open-design byte-identical to 2010c1d"
node -e "const fs=require('fs');const {execSync}=require('child_process');const g=(s)=>s.replace(/\r\n/g,'\n').match(/:root\{[\s\S]*?\n\}/)[0];const a=g(execSync('git show 2010c1d:docs/design/prototype/index.html',{encoding:'utf8'}));const b=g(fs.readFileSync('docs/design/prototype/index.html','utf8'));const cur=fs.readFileSync('docs/design/prototype/index.html','utf8').replace(/\r\n/g,'\n').split('\n');const startsAt11=cur[10].startsWith(':root{')&&cur[54]==='}';if(a!==b||!startsAt11){console.error('FAIL :root block changed or moved');process.exit(1)}console.log('OK   :root block byte-identical at lines 11-55 ('+a.split('\n').length+' lines)')"
```

Expected: every line `OK`.

- [ ] **Step 5: AC15 — the renders, once more, on the final tree**

```bash
node scripts/render_design_reference.mjs docs/design/prototype docs/design/reference
git status --short docs/design/reference
```

Expected: `9/9 captures verified`. If `git status` lists PNGs (Chrome's output is not guaranteed byte-stable), commit them: `git add docs/design/reference && git commit -m "design(reference): renders regenerated on the final tree"`. If the run prints anything but `9/9`, the failing capture names the task to revisit.

- [ ] **Step 6: Browser walkthrough (spec §4.6 step 2) — the checks above prove state; this proves the pixels**

Serve the prototype (`node -e "require('http').createServer((q,s)=>{const f=require('path').join('docs/design/prototype',q.url==='/'?'index.html':q.url);try{s.end(require('fs').readFileSync(f))}catch{s.statusCode=404;s.end()}}).listen(8765,()=>console.log('http://127.0.0.1:8765/'))"`) and open it in Chrome. Walk: idle → submit a starter with Extra passes 0 → submitted → running (chip `Running · pass 1 of 1`, no arc) → report; open `8f2c1d90` and watch the reviewer burst (Reviewing stays hollow, amber arc flows to Researching, `extra pass` tag appears, `pass 2 of 2`); open `c3d7e5f1` (grey arc to Writing, `redraft` tag, `pass 1 of 2` unchanged, `↺` on Writing when it completes); open `b41e77aa` → Evidence, click each chip, select a finding, `T04`, the refused row; open `2ad900b1`; DevTools → Rendering → emulate `prefers-reduced-motion: reduce` and repeat the extra-pass run (arc arrives lit, no dashes travelling); device toolbar 390×844 on the report stage (no horizontal page scroll; the table frame scrolls); collapse the sidebar at 1252×853; open `states.html` and drag the options-table frame right (the `Option` column stays put). **If anything needs a fix, make it in the task that owns the code, commit it, then re-run steps 1–5 of this task in full** (the README line count in `t13-docs.mjs` will catch a changed `index.html`; update README and commit if it does).

- [ ] **Step 7: Cleanup and summary**

```bash
rm -rf "$TEMP/dr-probe" "$TEMP"/dr-probe-* "$TEMP"/dr-replay-*
git status --short          # empty
git log --oneline 2010c1d..HEAD
```

Report, in the implementation summary: the `rg` result (nothing printed), `25/25` probes, the replay check line (`checked 20 emitted event types, 100 emitted keys + handler reads` / `OK`), `frozen.mjs` all `OK`, `t13-docs.mjs` `OK`, the `:root` and `open-design` identity lines, `9/9 captures verified`, the Chrome version (PowerShell `(Get-Item 'C:/Program Files/Google/Chrome/Application/chrome.exe').VersionInfo.ProductVersion` → 153.0.8010.53 on this machine), and the walkthrough's observations. Nothing from the replay step is committed (AC3: the script is not in the tree).

---

## Acceptance-criteria coverage

| AC | Satisfied by | Proven by |
|---|---|---|
| AC1 stale-name command prints nothing | Tasks 1–9, 11, 12, 13 (each strips its file's hits with a scoped `rg`; the five comment blocks no code touches are owned explicitly: `:718-720` Task 2, `:2549-2553` Task 3, `:583-589` and `:1214-1221` Task 4, `:434-443` Task 5) | Task 14 step 1 |
| AC2 seven `STAGES` ids, labels, captions | Task 3 | `t3-stages.js` |
| AC3 replay key check; script not in the tree | Task 3 (emitted types/keys), Task 4 (handler reads) | `replay_keys.py` + `check_keys.mjs`, re-run end to end in Task 14 step 3 with `git status` empty |
| AC4 extra-pass arc, inert reviewer completion, tag, `pass 2 of 2`, captions, counters reset | Task 4 (rows, header, counters), Task 5 (arc, stroke, anchors, tag) | `t4-rows.js`, `t5-extra.js` |
| AC5 redraft arc, `pass 1 of 2`, `↺` on Writing | Task 4 (rows, `↺`, draft rows), Task 5 (arc, tag) | `t4-rows.js`, `t5-redraft.js` |
| AC6 composer run at 0: `data-loop` never leaves `off`, chip `pass 1 of 1`; finished session | Task 2 (budget 0 request), Task 5 (running half), Task 7 (finished half) | `t2-zero.js`, `t5-zero.js`, `t7-finish.js` |
| AC7 composer: three models, no effort control, read-only line, stepper bounds, exact request body | Task 2 | `t2-composer.js`, `t2-zero.js` |
| AC8 five strip chips in order on submitted, running and report | Task 2 | `t2-composer.js` (`submitted`, `running`, `report`) |
| AC9 chip notes per fixture; failed → failed stage; partials → report stage | Task 1 | `t1-chips.js`, `t1-clause.js` |
| AC10 failed stage headline, facts, no download, `spineFailed` states, `not reached` | Task 6 | `t6-failed.js` |
| AC11 report card order, anchors, sources, muted link line, no stale blocks | Task 7 | `t7-report.js` |
| AC12 findings table fits at 1252 expanded/collapsed; phone frame; options table scroll + pinned column | Task 7 (index.html), Task 9 (states.html) | `t7-widths.js` (desktop + `--viewport=390x844`), `t9-states.js` |
| AC13 rail cards, 0.80 line, yellow 0.80 meters | Task 7 (rail, `9ea4c220`), Task 8 (`F03` detail meter) | `t7-report.js` (`yellow`), `t8-evidence.js` (`finding.overallCls === 'warn'`) |
| AC14 toggle, filters with counts, detail pane per kind | Task 8 | `t8-evidence.js`, `t8-keys.js` |
| AC15 `drConsole` additions; `9/9 captures verified`; files 01–09 | Tasks 4, 5, 8 (hooks), Task 10 (script + renders) | Task 10 step 4; Task 14 step 5 |
| AC16 api-gaps sections/order, E1 first with shape, five closed, 3.7, 4.1 downgraded, should-not-close tail; README no "Known defect" | Task 13 | `t13-docs.mjs` |
| AC17 frozen DESIGN.md regions byte-identical; `open-design/` untouched | Tasks 11–12 (edits confined to the change map); no task touches `open-design/` | `frozen.mjs`; `git diff --quiet 2010c1d -- docs/design/open-design` (Task 14 step 4) |
| AC18 `:root` block byte-identical | Tasks 2, 4, 5, 7, 8 append CSS only at the end of `<style>` | Task 14 step 4 `:root` check |

## Self-review

**1. Spec coverage.** §4.1 (active-row rule, header, loop tag, counters block, spine, arcs) → Tasks 3–5. §4.2 (statuses, chip, failed stage) → Tasks 1, 6. §4.3 (composer, strip, submitted, composer script) → Tasks 2, 3 (`loop: null` script), 4 (`COMPOSER_FINAL` on finish). §4.4 (report side, rail, Evidence side) → Tasks 7, 8. §4.5 (api-gaps) → Task 13. §4.6 (files, fixtures, `HALTED_EVENTS`, `drConsole`, captures, verification steps 1–4) → Tasks 1, 3, 4, 5, 8, 10, 14. §5 change map → Tasks 11–12, section by section, including the deliberate §3.6 deviation and the `:482-484` phrase. Every AC is in the table above. The spec's `(I)` copy is used verbatim except the two loop-tag sentences, which pluralise (recorded in Global Constraints). The recommendation's first paragraph gets a one-clause correctness edit beyond the spec's "second paragraph" (recorded in Task 11 step 4). `b41e77aa`'s question is a battery-storage question by controller decision (Global Constraints).

**2. Placeholder scan.** No "TBD", "TODO", "similar to Task N", or "add validation" remains; every code step shows the code, every doc step the text, every check the command and its expected output; there is no errata section — each step is the final version. The `README.md` line count is deliberately measured at execution time (the number depends on the finished edit) and the command that measures it is given.

**3. Type consistency.** `newRunState/applyEvent/marksFor/renderSpine/renderCounters` are defined in Task 4 and consumed by Task 6 with the same signatures; `setLoopState(v, arc)` and `renderLoopTag` (Task 5) are the two lines Task 5 inserts into Task 4's `paintRunning`; `statusNote/passText/fmtScore/notFoundClause` (Task 1) are consumed by Tasks 7–8; `COMPOSER_FINAL`, `session.final`, `session.script` (Tasks 1–2) are consumed by Tasks 3–4; `setView/currentView` (Task 8) are called from `populateReport` only after Task 8 adds them; `shot(name, expectedStage, extra)` (Task 10) keeps the two-argument calls valid; `drawLoop()` stays zero-argument for the resize handler. `COUNTER_ROWS` keys (`subTopics`, `toolCalls`, `findings`, `sources`, `verified`, `statements`, `review`) match every `data-counter` selector in the probes. The probe helper `chipText` is defined in the harness (Task 1) and used by Task 2.

**4. Review Focus.** The five failure modes are each pinned: burst equivalence (Task 4 step 12 rows/counters; Task 5 step 8 arc/tag), `max_iterations: 0` present (Task 2 step 7), null/zero evidence counts (Task 7 step 9), empty and plural not-found clause (Task 1 step 8), switching playback fixtures (Task 4 step 13 state; Task 5 step 8 arc/tag). Extra pins beyond the five: `researcher.tool_call` reads no keys (`check_keys.mjs`), reduced motion (Task 5 step 8), phone-width counters (Task 4 step 14), arrow-key selection (Task 8 step 7).

**5. Environment.** Every command was written for the agent's bash tool on this machine: Node checks instead of `bash <script>`; `rg` only where the agent shell provides it; the venv interpreter by absolute path; Chrome's version via PowerShell `VersionInfo`; every probe removes its Chrome profile and Task 14 sweeps `$TEMP/dr-probe`, `$TEMP/dr-probe-*` and `$TEMP/dr-replay-*`.

## Execution notes for the controller

- Recommended execution: **subagent-driven**, one fresh implementer per task and a reviewer gate after each — fourteen tasks share one 3,000-line file whose later tasks depend on the exact function names of earlier ones, and a shipped mistake here becomes the contract sub-project 3 builds against.
- Implementation agents: `sp-sonnet-implementer`-class implementers and `sp-reviewer`-class reviewers (per the user's standing routing ruling); no DeepSeek-backed role. The plan itself names no model as a role.
- **Tasks run strictly one after another, 1 → 14**, in this single worktree: Tasks 1–8 edit the same file, Task 10 renders the finished prototype, Task 13 measures it, and every commit takes the same `.git/index.lock`. No two tasks run at the same time.
