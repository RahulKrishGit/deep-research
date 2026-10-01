// @vitest-environment node — reads the replay captures (same reason as run-state.test.ts).
// notes-progress-report spec §8.2, AC32: the step a stop records is the row the page shows as active.
// This recomputes the page's own rule (`run.active`, lib/run-state.ts) after every prefix of every
// captured replay and compares it with test/fixtures/active-rows.json, which
// tests/test_api/test_stop.py::test_active_row_matches_web_rule holds the API's `active_row` to.
// After `npm run capture:events` re-records the captures, rewrite the file from the page's rule —
// `WRITE_ACTIVE_ROWS=1 npx vitest run test/active-row.test.ts` — review the diff, and run both tests.
import { readdirSync, readFileSync, writeFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import type { ResearchEvent } from "../lib/api";
import { applyEvent, newRunState, toRunEvent } from "../lib/run-state";

type Run = [string | null, number];
const fixtures = fileURLToPath(new URL("./fixtures/", import.meta.url));
const captures = path.join(fixtures, "events");
const goldenPath = path.join(fixtures, "active-rows.json");
const cases = readdirSync(captures).filter((name) => name.endsWith(".json")).map((name) => name.slice(0, -".json".length)).sort();

function activeRows(events: ResearchEvent[]): (string | null)[] {
  const run = newRunState();
  return [run.active, ...events.map((event) => { applyEvent(run, toRunEvent(event)); return run.active; })];
}
function runLength(rows: (string | null)[]): Run[] {
  const out: Run[] = [];
  for (const row of rows) {
    const last = out[out.length - 1];
    if (last && last[0] === row) last[1] += 1; else out.push([row, 1]);
  }
  return out;
}
const computed: Record<string, Run[]> = Object.fromEntries(cases.map((id) => {
  const capture = JSON.parse(readFileSync(path.join(captures, id + ".json"), "utf8")) as { events: ResearchEvent[] };
  return [id, runLength(activeRows(capture.events))];
}));
if (process.env.WRITE_ACTIVE_ROWS === "1") {
  writeFileSync(goldenPath, "{\n" + cases.map((id) => ` ${JSON.stringify(id)}: ${JSON.stringify(computed[id])}`).join(",\n") + "\n}\n");
}
const golden = JSON.parse(readFileSync(goldenPath, "utf8")) as Record<string, Run[]>;

describe("the page's active row after every prefix of every captured replay (AC32)", () => {
  it("covers every capture, and nothing else", () => {
    expect(Object.keys(golden).sort()).toEqual(cases);
  });
  for (const id of cases) {
    it(id, () => {
      expect(computed[id]).toEqual(golden[id]);
      expect(computed[id].reduce((sum, [, count]) => sum + count, 0)).toBe(JSON.parse(readFileSync(path.join(captures, id + ".json"), "utf8")).events.length + 1);
    });
  }
});
