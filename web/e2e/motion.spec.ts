// Motion of the step briefs: every transition they start, recorded
// with the delay and duration it started with, against the expected timings — and none of them travel
// or change height under reduced motion.
import { expect, test } from "@playwright/test";
import { installMotionRecorder, motion, submit, waitTerminal, type MotionRecord } from "./support";

const only = (records: MotionRecord[], where: Partial<MotionRecord>) =>
  records.filter((r) => Object.entries(where).every(([k, v]) => r[k as keyof MotionRecord] === v));
const timings = (records: MotionRecord[]) => [...new Set(records.map((r) => `${r.delay}/${r.duration}`))].sort();

test("the hand-off, a reader's open and close and the drawn check keep their timings", async ({ page, context, request }) => {
  await installMotionRecorder(page);
  // The one-time check holds the run at needs_input until the page answers, so the page is on the session
  // before Planning runs: Planning completes 1.1 s into the replay, and without the wait a page that loads
  // later (about 1.8 s here) first paints Planning done and never sees its checks draw.
  await context.setExtraHTTPHeaders({ "X-Replay-Clarify": "on" });
  const id = await submit(page, "q");
  await expect(page.locator("#clarifyCard")).toBeVisible({ timeout: 10_000 });
  await page.locator("#clarifyCard").getByRole("button", { name: "Just start" }).click();
  const planning = page.locator('#spine li[data-stage="planner"]');
  await expect(planning).toHaveAttribute("data-state", "done", { timeout: 10_000 });
  await page.waitForTimeout(2_100); // any hand-off roles from the first render's neighbours have cleared
  // Planning's own live transitions (the surplus skeleton's fade-out, 520/200, among them) come before the
  // reader's click; the open and close assertions below read what the click started.
  const beforeClick = (await motion(page)).length;
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
  // Row k+1 (Evaluating sources) as the "to": node fills and height opens at 600ms, its three lines (the
  // lead, the bar, the split) rise from 900ms, 60ms apart.
  const to = only(records, { stage: "source_evaluator", handoff: "to" });
  expect(timings(only(to, { part: "bullet", prop: "background-color" }))).toEqual(["600/420"]);
  expect(timings(only(to, { part: "ps-x", prop: "grid-template-rows" }))).toEqual(["600/420"]);
  expect(timings(only(to, { part: "line", prop: "opacity" }))).toEqual(["1020/240", "900/240", "960/240"]);
  expect(timings(only(to, { part: "line", prop: "transform" }))).toEqual(["1020/240", "900/240", "960/240"]);

  // Reviewing's hand-offs. In the default case the first is the extra pass: graph.route.decided
  // starts a 2 s hold on Reviewing's checks and verdict, but at replay pacing the extra pass (0.9 s of paced
  // events) ends before the hold does, so the run is already past the row it went back to and BriefSpine hands
  // over without the hold: Reviewing's first fold carries no role. Its only from-role fold is the one before
  // Publishing, so `firstFrom` is that one and the window below is Reviewing's final run. (The hold itself is
  // covered by progress.spec.ts's loop-route case and brief-spine.test.tsx.) Between the last to-role transition (as
  // the review lands) and that fold nothing on its row moves (it is never painted pending)…
  const onReviewing = records.filter((r) => r.stage === "report_reviewer");
  const firstFrom = onReviewing.findIndex((r) => r.handoff === "from");
  const lastTo = onReviewing.slice(0, Math.max(firstFrom, 0)).map((r) => r.handoff).lastIndexOf("to");
  expect(firstFrom).toBeGreaterThan(0);
  // …but the route decision's verdict, which cross-fades into the status line:
  // its `xf` records are opacity over 200 ms and the `.xf.rise` 5px lift over 420 ms.
  expect(onReviewing.slice(lastTo + 1, firstFrom).filter((r) => r.part !== "xf")).toEqual([]);
  // That from-role fold keeps the from-role timings: the subtitle cross-fade and the connector fill run, and
  // its lines and height fold only as far as they had opened — before Publishing, Reviewing is active for
  // about four paced events, less than its own 600/900 ms opening delays — so those are held to their
  // timing, not their presence.
  const reviewing = onReviewing.filter((r) => r.handoff === "from");
  expect(timings(only(reviewing, { part: "m-out", prop: "opacity" }))).toEqual(["260/200"]);
  expect(timings(only(reviewing, { part: "connector", prop: "transform" }))).toEqual(["180/620"]);
  expect(timings(only(reviewing, { part: "line", prop: "opacity" })).filter((x) => x !== "0/180")).toEqual([]);
  expect(only(reviewing, { part: "line", prop: "transform" })).toEqual([]);
  expect(timings(only(reviewing, { part: "ps-x", prop: "grid-template-rows" })).filter((x) => x !== "100/420")).toEqual([]);

  // A reader's open: height at once over 420ms, then the status line and the three slots rise 60ms
  // apart from 280ms (the surplus fourth slot has left the layout).
  const readers = records.slice(beforeClick);
  const opened = only(readers, { stage: "planner", handoff: null, open: "1" });
  expect(timings(only(opened, { part: "ps-x", prop: "grid-template-rows" }))).toEqual(["0/420"]);
  expect(timings(only(opened, { part: "line", prop: "opacity" }))).toEqual(["280/240", "340/240", "400/240", "460/240"]);
  // …and close: the lines fade 160ms with no stagger, then the height closes at 160ms.
  const closed = only(readers, { stage: "planner", handoff: null, open: "0" });
  expect(timings(only(closed, { part: "line", prop: "opacity" }))).toEqual(["0/160"]);
  expect(timings(only(closed, { part: "ps-x", prop: "grid-template-rows" }))).toEqual(["160/420"]);

  // A topic done: the ✓ draws over 360ms after 80ms; the dot fades over 200ms.
  expect(timings(only(records, { stage: "researcher", part: "check", prop: "stroke-dashoffset" }))).toEqual(["80/360"]);
  expect(timings(only(records, { stage: "planner", part: "check", prop: "stroke-dashoffset" }))).toEqual(["80/360"]);
  expect(timings(only(records, { part: "dot", prop: "opacity" }))).toEqual(["0/200"]);
  // Reviewing's five checks land 60ms apart.
  expect(timings(only(records, { stage: "report_reviewer", part: "check", prop: "stroke-dashoffset" })))
    .toEqual(["140/360", "200/360", "260/360", "320/360", "80/360"]);
});

test.describe("reduced motion", () => {
  test.use({ reducedMotion: "reduce" });
  test("nothing travels and no height animates; lines only fade, 160ms, together", async ({ page, request }) => {
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
    // Nothing waits on the hand-off timings either: state reads at once (a delayed transition still
    // fires with a 0ms duration, so a surviving delay would show as a record with delay > 0), and the
    // ✓ appears without drawing, so no check transition runs at all.
    expect(records.filter((r) => r.delay > 0)).toEqual([]);
    expect(only(records, { part: "check" })).toEqual([]);
  });
});
