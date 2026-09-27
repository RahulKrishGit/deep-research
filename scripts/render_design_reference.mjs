#!/usr/bin/env node
/**
 * Render reference screenshots of the Open Design prototype at its designed
 * 1252x853 viewport, driving all five stages through the page's own
 * window.drConsole review hook.
 *
 * Every capture asserts the stage it actually landed on, so a screenshot can
 * never silently depict the wrong screen.
 *
 * usage: node render.mjs <protoDir> <outDir> [chromePath]
 */
import { spawn } from 'node:child_process';
import { createServer } from 'node:http';
import { readFile, writeFile, mkdir, mkdtemp } from 'node:fs/promises';
import { existsSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';

const PROTO_DIR = process.argv[2];
const OUT_DIR = process.argv[3];
const CHROME = process.argv[4] || 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe';
if (!PROTO_DIR || !OUT_DIR) {
  console.error('usage: node render.mjs <protoDir> <outDir> [chromePath]');
  process.exit(2);
}

const W = 1252, H = 853;
const PHONE_W = 390, PHONE_H = 844;
const QUESTION = 'What is the current state of grid-scale battery storage?';
const MIME = {
  '.html': 'text/html; charset=utf-8', '.css': 'text/css; charset=utf-8',
  '.js': 'text/javascript; charset=utf-8', '.svg': 'image/svg+xml', '.png': 'image/png',
};
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

/* ---------------------------------------------------------------- server -- */
const server = createServer(async (req, res) => {
  try {
    let rel = decodeURIComponent(new URL(req.url, 'http://x').pathname);
    if (rel === '/') rel = '/index.html';
    const file = path.join(PROTO_DIR, rel);
    const buf = await readFile(file);
    res.writeHead(200, { 'content-type': MIME[path.extname(file)] || 'application/octet-stream' });
    res.end(buf);
  } catch {
    res.writeHead(404); res.end('not found');
  }
});
await new Promise((r) => server.listen(0, '127.0.0.1', r));
const origin = `http://127.0.0.1:${server.address().port}`;

/* --------------------------------------------------------------- chrome --- */
const profile = await mkdtemp(path.join(tmpdir(), 'od-render-'));
const chrome = spawn(CHROME, [
  '--headless=new',
  '--remote-debugging-port=0',
  `--user-data-dir=${profile}`,
  '--disable-gpu', '--no-first-run', '--no-default-browser-check',
  '--disable-extensions', '--hide-scrollbars',
  '--force-device-scale-factor=1',
  `--window-size=${W},${H}`,
  'about:blank',
], { stdio: 'ignore', detached: true });

const portFile = path.join(profile, 'DevToolsActivePort');
let devPort = null;
for (let i = 0; i < 150; i++) {
  if (existsSync(portFile)) {
    const line = (await readFile(portFile, 'utf8')).split('\n')[0].trim();
    if (line) { devPort = line; break; }
  }
  await sleep(100);
}
if (!devPort) { console.error('FATAL: chrome never reported a devtools port'); process.exit(1); }

const targets = await (await fetch(`http://127.0.0.1:${devPort}/json/list`)).json();
const target = targets.find((t) => t.type === 'page');
if (!target) { console.error('FATAL: no page target'); process.exit(1); }

const ws = new WebSocket(target.webSocketDebuggerUrl);
await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });

let seq = 0;
const pending = new Map();
ws.onmessage = (ev) => {
  const msg = JSON.parse(ev.data);
  if (msg.id && pending.has(msg.id)) {
    const { resolve, reject } = pending.get(msg.id);
    pending.delete(msg.id);
    msg.error ? reject(new Error(`${msg.error.message}`)) : resolve(msg.result);
  }
};
const send = (method, params = {}) => new Promise((resolve, reject) => {
  const id = ++seq;
  pending.set(id, { resolve, reject });
  ws.send(JSON.stringify({ id, method, params }));
});

const evaluate = async (expression) => {
  const r = await send('Runtime.evaluate', { expression, returnByValue: true, awaitPromise: true });
  if (r.exceptionDetails) throw new Error(`page threw: ${r.exceptionDetails.text} ${r.exceptionDetails.exception?.description || ''}`);
  return r.result.value;
};

/* ------------------------------------------------------------- lifecycle -- */
async function setViewport(width, height) {
  await send('Emulation.setDeviceMetricsOverride', {
    width, height, deviceScaleFactor: 1, mobile: false,
  });
}

async function loadClean(file) {
  await send('Page.navigate', { url: `${origin}/${file}` });
  // establish the origin, wipe persisted console state, then boot for real
  for (let i = 0; i < 100; i++) {
    if (await evaluate('document.readyState === "complete"').catch(() => false)) break;
    await sleep(60);
  }
  await evaluate('try{localStorage.clear()}catch(e){}');
  await send('Page.navigate', { url: `${origin}/${file}` });
  for (let i = 0; i < 150; i++) {
    const ready = await evaluate('typeof window.drConsole !== "undefined"').catch(() => false);
    if (ready) break;
    await sleep(60);
  }
}

async function waitForStage(stage, timeoutMs = 15000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const cur = await evaluate('window.drConsole.stage()').catch(() => null);
    if (cur === stage) return true;
    await sleep(80);
  }
  return false;
}

const results = [];
async function shot(name, expectedStage) {
  const actual = await evaluate('window.drConsole.stage()').catch(() => '?');
  const png = await send('Page.captureScreenshot', { format: 'png', captureBeyondViewport: false });
  const file = path.join(OUT_DIR, `${name}.png`);
  await writeFile(file, Buffer.from(png.data, 'base64'));
  const ok = expectedStage === null || actual === expectedStage;
  results.push({ name, expected: expectedStage, actual, ok, file });
  console.log(`${ok ? 'OK  ' : 'FAIL'}  ${name.padEnd(14)} stage=${String(actual).padEnd(10)} expected=${expectedStage ?? '-'}`);
}

async function save(name) {
  const png = await send('Page.captureScreenshot', { format: 'png', captureBeyondViewport: false });
  await writeFile(path.join(OUT_DIR, `${name}.png`), Buffer.from(png.data, 'base64'));
}

/* ------------------------------------------------------------------ run --- */
await mkdir(OUT_DIR, { recursive: true });
await send('Page.enable');
await send('Runtime.enable');

// journey: idle -> submitted -> running -> report, in one continuous session
await setViewport(W, H);
await loadClean('index.html');
await sleep(700);
await shot('01-idle', 'idle');

await evaluate(`window.drConsole.submit(${JSON.stringify(QUESTION)})`);
if (await waitForStage('submitted')) { await sleep(450); await shot('02-submitted', 'submitted'); }
else console.log('FAIL  02-submitted   never reached "submitted" (beat is ~2.2s; it may have passed)');

if (await waitForStage('running')) { await sleep(1800); await shot('03-running', 'running'); }
else console.log('FAIL  03-running     never reached "running"');

const finished = await evaluate('(function(){ try{ window.drConsole.finish(); return true }catch(e){ return String(e) } })()');
if (finished === true && await waitForStage('report')) { await sleep(1300); await shot('04-report', 'report'); }
else console.log(`FAIL  04-report      finish() -> ${finished}`);

// failed stage: open the seeded failed session
await loadClean('index.html');
const failedId = await evaluate('(window.drConsole.sessions.find(s=>s.status==="failed")||{}).id || null');
if (failedId) {
  await evaluate(`window.drConsole.open(${JSON.stringify(failedId)})`);
  if (await waitForStage('failed')) { await sleep(900); await shot('05-failed', 'failed'); }
  else console.log('FAIL  05-failed      never reached "failed"');
} else {
  console.log('FAIL  05-failed      no seeded session with status "failed"');
}

// states.html is a static fixture: capture it whole
await setViewport(W, H);
await send('Page.navigate', { url: `${origin}/states.html` });
for (let i = 0; i < 120; i++) {
  if (await evaluate('document.readyState === "complete"').catch(() => false)) break;
  await sleep(60);
}
await sleep(600);
const fullH = await evaluate('Math.max(document.body.scrollHeight, document.documentElement.scrollHeight)');
await setViewport(W, Math.min(Math.max(fullH, H), 12000));
await sleep(400);
await save('06-states');
results.push({ name: '06-states', expected: null, actual: `${W}x${fullH}`, ok: true, file: '06-states.png' });
console.log(`OK    06-states      full page ${W}x${fullH}`);

// phone width, to document the drawer breakpoint
await setViewport(PHONE_W, PHONE_H);
await loadClean('index.html');
await sleep(800);
await shot('07-idle-phone', 'idle');

/* ------------------------------------------------------------- teardown --- */
console.log('\n--- summary ---');
for (const r of results) console.log(`${r.ok ? 'OK  ' : 'FAIL'}  ${r.name.padEnd(14)} ${r.actual}`);
const bad = results.filter((r) => !r.ok);
console.log(`\n${results.length - bad.length}/${results.length} captures verified`);
if (bad.length) console.log('MISMATCHED: ' + bad.map((b) => b.name).join(', '));

try { ws.close(); } catch {}
try { process.kill(-chrome.pid); } catch { try { chrome.kill(); } catch {} }
server.close();
process.exit(bad.length ? 1 : 0);
