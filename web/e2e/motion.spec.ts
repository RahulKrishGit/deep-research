// live-briefs spec §4.3 motion table (D1, D3; AC7): every transition the step briefs start, recorded
// with the delay and duration it started with, against the spec's timings — and none of them travel
// or change height under reduced motion.
import { expect, test } from "@playwright/test";
import { installMotionRecorder, motion, submit, waitTerminal, type MotionRecord } from "./support";

const only = (records: MotionRecord[], where: Partial<MotionRecord>) =>
  records.filter((r) => Object.entries(where).every(([k, v]) => r[k as keyof MotionRecord] === v));
const timings = (records: MotionRecord[]) => [...new Set(records.map((r) => `${r.delay}/${r.duration}`))].sort();

test("the hand-off (3B), a reader's open and close (1A) and the drawn check keep the spec's timings", async ({ page, request }) => {
  await installMotionRecorder(page);
  const id = await submit(page, "q");
  const planning = page.locator('#spine li[data-stage="planner"]');
  await expect(planning).toHaveAttribute("data-state", "done", { timeout: 10_000 });
  await page.waitForTimeout(2_100); // any hand-off roles from the first render's neighbours have cleared
  await planning.locator("button.ps-toggle").click();
  await expect(planning).toHaveAttribute("data-open", "1");
  await page.waitForTimeout(700);
  await planning.locator("button.ps-toggle").click();
  await expect(planning).toHaveAttribute("data-open", "0");
  await waitTerminal(request, id);
  const records = await motion(page);

  // Row k (Researching) as the hand-off's "from": lines fade 180ms at 0, height closes at 100ms,
  // the subtitle cross-fades at 260ms, the connector below it fills at 180ms.
  const from = only(records, { stage: "researcher", handoff: "from" });
  expect(timings(only(from, { part: "line", prop: "opacity" }))).toEqual(["0/180"]);
  expect(only(from, { part: "line", prop: "transform" })).toEqual([]);
  expect(timings(only(from, { part: "ps-x", prop: "grid-template-rows" }))).toEqual(["100/420"]);
  expect(timings(only(from, { part: "m-out", prop: "opacity" }))).toEqual(["260/200"]);
  expect(timings(only(from, { part: "connector", prop: "transform" }))).toEqual(["180/620"]);
  // Row k+1 (Evaluating sources) as the "to": node fills and height opens at 600ms, its one line rises at 900ms.
  const to = only(records, { stage: "source_evaluator", handoff: "to" });
  expect(timings(only(to, { part: "bullet", prop: "background-color" }))).toEqual(["600/420"]);
  expect(timings(only(to, { part: "ps-x", prop: "grid-template-rows" }))).toEqual(["600/420"]);
  expect(timings(only(to, { part: "line", prop: "opacity" }))).toEqual(["900/240"]);
  expect(timings(only(to, { part: "line", prop: "transform" }))).toEqual(["900/240"]);

  // Reviewing → Publishing. graph.route.decided moves the active row one event before Reviewing's own
  // completion (150 ms apart in replay); Reviewing stays as it was — active, open — until then, so nothing
  // on its row moves between its last to-role transition and its first from-role one (it is never painted pending)…
  const onReviewing = records.filter((r) => r.stage === "report_reviewer");
  const firstFrom = onReviewing.findIndex((r) => r.handoff === "from");
  const lastTo = onReviewing.slice(0, Math.max(firstFrom, 0)).map((r) => r.handoff).lastIndexOf("to");
  expect(firstFrom).toBeGreaterThan(0);
  expect(onReviewing.slice(lastTo + 1, firstFrom)).toEqual([]);
  // …and its completion folds it with the from-role timings. The subtitle cross-fade and the connector fill
  // always run; its lines and height fold only as far as they had opened — Reviewing is active for about
  // four paced events, less than its own 600/900 ms opening delays — so those are held to their timing,
  // not their presence.
  const reviewing = onReviewing.filter((r) => r.handoff === "from");
  expect(timings(only(reviewing, { part: "m-out", prop: "opacity" }))).toEqual(["260/200"]);
  expect(timings(only(reviewing, { part: "connector", prop: "transform" }))).toEqual(["180/620"]);
  expect(timings(only(reviewing, { part: "line", prop: "opacity" })).filter((x) => x !== "0/180")).toEqual([]);
  expect(only(reviewing, { part: "line", prop: "transform" })).toEqual([]);
  expect(timings(only(reviewing, { part: "ps-x", prop: "grid-template-rows" })).filter((x) => x !== "100/420")).toEqual([]);

  // A reader's open (1A): height at once over 420ms, then the three titles rise 60ms apart from 280ms.
  const opened = only(records, { stage: "planner", handoff: null, open: "1" });
  expect(timings(only(opened, { part: "ps-x", prop: "grid-template-rows" }))).toEqual(["0/420"]);
  expect(timings(only(opened, { part: "line", prop: "opacity" }))).toEqual(["280/240", "340/240", "400/240"]);
  // …and close: the lines fade 160ms with no stagger, then the height closes at 160ms.
  const closed = only(records, { stage: "planner", handoff: null, open: "0" });
  expect(timings(only(closed, { part: "line", prop: "opacity" }))).toEqual(["0/160"]);
  expect(timings(only(closed, { part: "ps-x", prop: "grid-template-rows" }))).toEqual(["160/420"]);

  // A topic done: the ✓ draws over 360ms after 80ms; the dot fades over 200ms.
  expect(timings(only(records, { part: "check", prop: "stroke-dashoffset" }))).toEqual(["80/360"]);
  expect(timings(only(records, { part: "dot", prop: "opacity" }))).toEqual(["0/200"]);
});

test.describe("reduced motion", () => {
  test.use({ reducedMotion: "reduce" });
  test("nothing travels and no height animates; lines only fade, 160ms, together (AC7)", async ({ page, request }) => {
    await installMotionRecorder(page);
    const id = await submit(page, "q");
    const planning = page.locator('#spine li[data-stage="planner"]');
    await expect(planning).toHaveAttribute("data-state", "done", { timeout: 10_000 });
    await planning.locator("button.ps-toggle").click();
    await expect(planning).toHaveAttribute("data-open", "1");
    await waitTerminal(request, id);
    const records = await motion(page);
    expect(records.filter((r) => r.prop === "transform")).toEqual([]);
    expect(records.filter((r) => r.prop === "grid-template-rows")).toEqual([]);
    const lines = only(records, { part: "line" });
    expect(lines.length).toBeGreaterThan(0);
    expect(timings(lines)).toEqual(["0/160"]);
    expect([...new Set(lines.map((r) => r.prop))]).toEqual(["opacity"]);
  });
});
