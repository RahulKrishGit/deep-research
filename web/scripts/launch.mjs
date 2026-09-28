// usage:
//   node scripts/launch.mjs start <name> <port> <ready-path> "<command>"
//       → exit 1 if the port already answers (a stale server); else spawn the command in the current directory with the
//         current environment (set variables on the same shell line), detached, and wait until http://127.0.0.1:<port><ready-path>
//         answers 200 (120 s); write .launch-<name>.pid
//   node scripts/launch.mjs stop <name> <port>
//       → taskkill /PID <pid> /T /F, then poll until the port refuses connections (10 s); exit 1 loudly if it still answers
import { execSync, spawn } from "node:child_process";
import { existsSync, readFileSync, unlinkSync, writeFileSync } from "node:fs";

const [action, name, portArg, ...rest] = process.argv.slice(2);
const port = Number(portArg);
const pidFile = `.launch-${name}.pid`;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
// The status the port answers with, or null when the connection is refused.
const answers = async (path) => { try { return (await fetch(`http://127.0.0.1:${port}${path}`)).status; } catch { return null; } };

if (action === "start") {
  const [readyPath, command] = rest;
  if (!name || !port || !readyPath || !command) { console.error('usage: launch.mjs start <name> <port> <ready-path> "<command>"'); process.exit(2); }
  if ((await answers("/")) !== null) { console.error(`port ${port} already answers — a stale server is running; stop it first`); process.exit(1); }
  // detached: a non-detached child sits in a job object that dies with this process and orphans its grandchildren.
  const child = spawn(command, { shell: true, detached: true, windowsHide: true, stdio: "ignore" });
  child.unref();
  writeFileSync(pidFile, String(child.pid));
  for (let i = 0; i < 240; i++) {
    if ((await answers(readyPath)) === 200) { console.log(`up: http://127.0.0.1:${port}${readyPath} (pid ${child.pid} → ${pidFile})`); process.exit(0); }
    await sleep(500);
  }
  console.error(`http://127.0.0.1:${port}${readyPath} did not answer 200 within 120 s (pid ${child.pid} is in ${pidFile}; run stop)`);
  process.exit(1);
} else if (action === "stop") {
  if (!name || !port) { console.error("usage: launch.mjs stop <name> <port>"); process.exit(2); }
  const pid = existsSync(pidFile) ? readFileSync(pidFile, "utf8").trim() : null;
  let killed = false;
  if (pid === null) console.error(`no ${pidFile}: nothing to kill by PID; checking the port`);
  else { try { execSync(`taskkill /PID ${pid} /T /F`, { stdio: "ignore" }); killed = true; } catch { console.error(`taskkill /PID ${pid} /T /F failed — the recorded process is already gone; checking the port`); } }
  for (let i = 0; i < 20; i++) {
    if ((await answers("/")) === null) {
      if (pid !== null) unlinkSync(pidFile);
      console.log(`stopped: port ${port} refuses connections${pid === null ? "" : killed ? ` (pid ${pid} and its tree)` : ` (pid ${pid} was already gone)`}`);
      process.exit(0);
    }
    await sleep(500);
  }
  const why = pid === null ? " and there was no PID file" : killed ? ` although pid ${pid} was killed` : ` and pid ${pid} was already gone`;
  console.error(`FAILED: port ${port} still answers after 10 s${why} — a server is still running; find its owner: powershell "Get-NetTCPConnection -LocalPort ${port} -State Listen | Select OwningProcess" then taskkill /PID <owner> /T /F`);
  process.exit(1);
} else { console.error('usage: launch.mjs start <name> <port> <ready-path> "<command>" | stop <name> <port>'); process.exit(2); }
