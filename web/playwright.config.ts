import { defineConfig, devices } from "@playwright/test";
import { execSync } from "node:child_process";
import { existsSync, mkdirSync, rmSync } from "node:fs";
import path from "node:path";

// Paths resolve from process.cwd() (= web/), never __dirname, so this file stays valid as an ES module.
const web = process.cwd();
const repo = path.resolve(web, "..");
// The venv lives at the main checkout root; inside a .worktrees/* tree the default does not exist and
// DEEP_RESEARCH_PYTHON is required. Quoted below: every interpreter path on this machine has spaces.
const python = process.env.DEEP_RESEARCH_PYTHON ?? path.resolve(repo, ".venv", "Scripts", "python.exe");
if (!existsSync(python)) {
  throw new Error(`DEEP_RESEARCH_PYTHON must point at the venv interpreter (…/.venv/Scripts/python.exe); ${python} does not exist`);
}
// M8: the API server's temp root lives here — outside test-results/, which Playwright clears at
// the start of a run — so a forced kill leaks nothing into %TEMP% and nothing is swept away
// mid-run. That same "forced kill skips cleanup" is why this directory accumulates one leaked
// replay root per local run otherwise: clear it before a run starts. This runs at config
// module-eval time, not in Playwright's own `globalSetup` hook — plugin/webServer setup runs
// *before* globalSetup in Playwright's own task order, so a globalSetup-based rm would race the
// webServer process this same run is about to start. Guarded by an env flag (the same `??=`
// idiom DEEP_RESEARCH_DEAD_PORT uses below) so a worker process re-evaluating this file never
// re-clears a directory the already-started webServer is actively using.
const tmp = path.join(web, ".e2e-tmp");
if (!process.env.DEEP_RESEARCH_E2E_TMP_CLEARED) {
  rmSync(tmp, { recursive: true, force: true });
  process.env.DEEP_RESEARCH_E2E_TMP_CLEARED = "1";
}
mkdirSync(tmp, { recursive: true });
// One closed, ordinary port for the "API unreachable" app, reserved once: ??= keeps the value the
// worker processes inherit when they re-evaluate this file. Never port 1 (the Fetch bad-port list).
process.env.DEEP_RESEARCH_DEAD_PORT ??= execSync(
  `node -e "const s=require('net').createServer().listen(0,()=>{process.stdout.write(String(s.address().port));s.close()})"`,
).toString().trim();
const deadPort = process.env.DEEP_RESEARCH_DEAD_PORT;
const API = "http://127.0.0.1:8010";

export default defineConfig({
  testDir: "./e2e",
  timeout: 90_000,
  expect: { timeout: 15_000 },
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: [["list"]],
  outputDir: "test-results",
  use: { baseURL: "http://127.0.0.1:3010", ...devices["Desktop Chrome"], viewport: { width: 1252, height: 853 } },
  projects: [
    { name: "chromium", testIgnore: /visual\.spec\.ts/ },
    { name: "visual", testMatch: /visual\.spec\.ts/ },
  ],
  webServer: [
    {
      command: `${JSON.stringify(python)} -m deep_research.api --mode replay --port 8010`,
      url: `${API}/research`, cwd: repo, timeout: 120_000, reuseExistingServer: false,
      env: { PYTHONPATH: "src", PYTHONDONTWRITEBYTECODE: "1", TEMP: tmp, TMP: tmp },
    },
    { command: "npm run -s start -- --port 3010", url: "http://127.0.0.1:3010/", cwd: web, timeout: 120_000, reuseExistingServer: false, env: { DEEP_RESEARCH_API_URL: API } },
    { command: "npm run -s start -- --port 3011", url: "http://127.0.0.1:3011/", cwd: web, timeout: 120_000, reuseExistingServer: false, env: { DEEP_RESEARCH_API_URL: `http://127.0.0.1:${deadPort}` } },
  ],
});
