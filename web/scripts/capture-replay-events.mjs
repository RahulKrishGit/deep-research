// Record one replay session's frames and its final /status into test/fixtures/events/<case>.json.
// usage (with the API running in replay mode, ideally --replay-delay-ms 0):
//   DEEP_RESEARCH_API_URL=http://127.0.0.1:8010 node scripts/capture-replay-events.mjs <case-id> [<case-id> ...]
import { mkdirSync, writeFileSync } from "node:fs";
import path from "node:path";

const api = process.env.DEEP_RESEARCH_API_URL ?? "http://127.0.0.1:8010";
const cases = process.argv.slice(2);
if (!cases.length) { console.error("usage: capture-replay-events.mjs <case-id> ..."); process.exit(2); }
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const frames = (text) =>
  text.split(/\r?\n\r?\n/).filter((f) => f.includes("data: ")).map((f) =>
    JSON.parse(f.split(/\r?\n/).filter((l) => l.startsWith("data: ")).map((l) => l.slice(6)).join("\n")));

for (const caseId of cases) {
  const posted = await fetch(`${api}/research`, {
    method: "POST",
    headers: { "content-type": "application/json", "x-replay-case": caseId },
    body: JSON.stringify({ query: "capture", max_iterations: null, output_format: "markdown", config_overrides: {} }),
  });
  if (posted.status !== 202) throw new Error(`POST /research → ${posted.status}: ${await posted.text()}`);
  const { session_id } = await posted.json();
  const stream = await fetch(`${api}/research/${session_id}/stream`);
  const events = frames(await stream.text()); // the stream closes at the terminal status
  let status = null;
  for (let i = 0; i < 100; i++) {
    status = await (await fetch(`${api}/research/${session_id}/status`)).json();
    if (status.status !== "running") break;
    await sleep(100);
  }
  if (!status || status.status === "running") throw new Error(`${caseId}: the session did not finish`);
  const out = path.join("test", "fixtures", "events", `${caseId}.json`);
  mkdirSync(path.dirname(out), { recursive: true });
  const record = { case_id: caseId, captured_at: new Date().toISOString(), api_mode: stream.headers.get("x-deep-research-mode"), status, events };
  writeFileSync(out, JSON.stringify(record, null, 1) + "\n");
  console.log(`${out}: ${events.length} events, ${status.status}/${status.iteration}`);
}
