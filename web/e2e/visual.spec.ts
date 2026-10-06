import { mkdirSync } from "node:fs";
import path from "node:path";
import { expect, test, type Page } from "@playwright/test";
import { API, submit, waitTerminal } from "./support";

const CHECKPOINT = process.env.VISUAL_CHECKPOINT ?? "C4";
const dir = path.join("visual", CHECKPOINT);
mkdirSync(dir, { recursive: true });
const shoot = async (page: Page, name: string) => {
  await page.mouse.move(0, 0); // no hover state in a capture: a finished row highlights under the pointer
  await page.waitForTimeout(400);
  await page.screenshot({ path: path.join(dir, `${name}.png`), fullPage: true });
  const width = await page.evaluate(() => [document.scrollingElement!.scrollWidth, window.innerWidth]);
  expect(width[0], `${name}: no page-wide horizontal scroll`).toBeLessThanOrEqual(width[1]);
};
const PHONE = { width: 390, height: 844 };

/* What the Researching row shows right now, read in the page. `settled` is true only when no part of it is
   mid-change: every topic row (and the acknowledgement lines) is fully opaque with no running transition or
   finite animation under it, each mark has reached the end state its row's data-topic names (a ✓ has drawn and
   its ring has gone; a running dot is lit and the ring gone; a waiting ring is shown), and the head's subtitle
   is fully opaque with nothing moving. The running dot's halo repeats for good, so an infinite animation is not
   motion. `key` is what the row says (each topic's state and fact, and the subtitle), so two snapshots with
   one key show the same row. The subtitle's counts tween in script for 400 ms, so a settled row is also stable
   across two reads, which the callers check. */
const researchingSnapshot = (page: Page) => page.evaluate(() => {
  const li = document.querySelector('#spine li[data-stage="researcher"]');
  if (!li) return { settled: false, key: "", subtitle: "", rows: 0, done: 0, why: "no Researching row" };
  const css = (el: Element | null, prop: string) => (el ? getComputedStyle(el).getPropertyValue(prop) : "");
  const moving = (el: Element) => el.getAnimations({ subtree: true }).filter((a) => {
    const timing = a.effect?.getComputedTiming();
    return a.playState !== "finished" && timing?.iterations !== Infinity;
  }).length;
  const why: string[] = [];
  const topics = [...li.querySelectorAll<HTMLElement>(".ps-topics > .ln")];
  const facts = topics.map((row, i) => {
    const state = row.dataset.topic ?? "";
    const ring = Number(css(row.querySelector(".ring"), "opacity"));
    const dot = Number(css(row.querySelector(".dotc"), "opacity"));
    const tick = parseFloat(css(row.querySelector("svg path"), "stroke-dashoffset"));
    const marked = state === "done" ? ring === 0 && dot === 0 && tick === 0
      : state === "running" ? ring === 0 && dot === 1 && tick > 0
      : state === "waiting" ? ring === 1 && dot === 0 && tick > 0
      : true;
    if (css(row, "opacity") !== "1") why.push(`row ${i + 1} opacity ${css(row, "opacity")}`);
    if (css(row.querySelector(".mk"), "opacity") !== "1") why.push(`row ${i + 1} mark opacity ${css(row.querySelector(".mk"), "opacity")}`);
    if (!marked) why.push(`row ${i + 1} is ${state} but its mark is ring ${ring}, dot ${dot}, tick ${tick}`);
    if (moving(row) > 0) why.push(`row ${i + 1} is mid-transition`);
    return `${state}:${row.querySelector(".tf")?.textContent ?? ""}`;
  });
  for (const ack of li.querySelectorAll<HTMLElement>(".ln.ack")) {
    if (css(ack, "opacity") !== "1" || moving(ack) > 0) why.push("an acknowledgement line is mid-transition");
  }
  const head = li.querySelector(".ps-head");
  const live = li.querySelector(".m-live");
  if (css(live, "opacity") !== "1") why.push(`subtitle opacity ${css(live, "opacity")}`);
  if (head && moving(head) > 0) why.push("the head is mid-transition");
  if (topics.length === 0) why.push("no topic rows");
  const subtitle = live?.textContent ?? "";
  return { settled: why.length === 0, key: `${subtitle}|${facts.join("|")}`, subtitle, rows: topics.length, done: facts.filter((f) => f.startsWith("done:")).length, why: why.join("; ") };
});

/* Waits until the Researching row has settled and its text has held for two reads about 300 ms apart. */
const settleResearching = async (page: Page, name: string) => {
  const deadline = Date.now() + 20_000;
  let held: string | null = null;
  for (;;) {
    const snap = await researchingSnapshot(page);
    if (snap.settled && snap.key === held) return snap;
    held = snap.settled ? snap.key : null;
    if (Date.now() > deadline) throw new Error(`${name}: the Researching row never settled (${snap.why || "its text kept changing"}): ${snap.key}`);
    await page.waitForTimeout(300);
  }
};

/* A capture of the running Researching row: taken once the row has settled, and kept only if it was still the
   same settled row once the screenshot was done (an event landing during the shot sends it round again). */
const shootSettled = async (page: Page, name: string, subject: (snap: Awaited<ReturnType<typeof researchingSnapshot>>) => Promise<void> | void) => {
  for (let attempt = 1; attempt <= 8; attempt++) {
    const before = await settleResearching(page, name);
    await subject(before);
    await shoot(page, name);
    const after = await researchingSnapshot(page);
    if (after.settled && after.key === before.key) return;
  }
  throw new Error(`${name}: the Researching row kept changing under the screenshot`);
};

for (const [suffix, viewport] of [["", null], ["-phone", PHONE]] as const) {
  test.describe(`captures${suffix}`, () => {
    if (viewport) test.use({ viewport });

    test(`01-idle${suffix} / 07-idle-phone`, async ({ page }) => {
      await page.goto("/");
      await shoot(page, suffix ? "07-idle-phone" : "01-idle");
    });

    test(`02-submitted${suffix}, 03-running${suffix}, 09-running-extra-pass${suffix}`, async ({ page, request, context }) => {
      // 03 needs a Researching row that has stopped moving, and the replay never gives it one: it releases an event
      // every 150 ms, and the first done topic is followed by the next completion 750 ms later, which is less than
      // a ✓ drawing (440 ms) plus the settle checks and the shot need. So 02 and 03 come from a run held after its
      // second topic completes (two ✓ and one still reading), and 09 from a second run, held in its extra pass.
      await context.setExtraHTTPHeaders({ "X-Replay-Case": "missing-target-triggers-one-extra-pass", "X-Replay-Hold-After": "researcher.sub_topic.completed#2" });
      const held = await submit(page, "What is the current state of grid-scale battery storage?");
      await expect(page.locator("#stage-submitted")).toBeVisible();
      await shoot(page, `02-submitted${suffix}`);
      // 03-running is taken with the Researching brief open and a topic done.
      await expect(page.locator('#spine li[data-stage="researcher"][data-open="1"] .ps-topics [data-topic="done"]')).toHaveCount(2, { timeout: 15_000 });
      // The ✓ and fact of each done topic have settled together, and the subtitle has stopped counting, before the shot.
      await shootSettled(page, `03-running${suffix}`, (snap) => expect(snap.done, "03-running shows its done topics").toBe(2));
      expect((await request.post(`${API}/research/${held}/stop`)).status()).toBe(202);
      // Held after the extra pass's own researcher.sub_topic.started (the 4th: the first pass starts three): at
      // replay pacing the extra pass ends before Reviewing's hold does, so the run would be past Researching and it
      // would never reopen (as in briefs.spec.ts's arc test). Held here, the reopened row shows its topic.
      await context.setExtraHTTPHeaders({ "X-Replay-Case": "missing-target-triggers-one-extra-pass", "X-Replay-Hold-After": "researcher.sub_topic.started#4" });
      const id = await submit(page, "What is the current state of grid-scale battery storage?");
      await expect(page.locator('#spineWrap[data-loop="settled"][data-arc="extra_pass"]')).toBeVisible({ timeout: 30_000 });
      // Reviewing holds its verdict for HANDOFF_HOLD_MS first; 09 shows Researching reopened.
      await expect(page.locator('#spine li[data-stage="researcher"][data-open="1"]')).toBeVisible();
      // The row is still opening when data-open flips: its lines rise after the height, so wait for the reason line.
      await expect(page.locator('#spine li[data-stage="researcher"] .b-why')).toHaveCSS("opacity", "1");
      // The extra pass's one topic, running: the reopened row with its topic row.
      const topic = page.locator('#spine li[data-stage="researcher"] .ps-topics > .ln');
      await expect(topic).toHaveCount(1);
      await expect(topic).toHaveAttribute("data-topic", "running");
      await expect(topic).toHaveCSS("opacity", "1");
      await shoot(page, `09-running-extra-pass${suffix}`);
      expect((await request.post(`${API}/research/${id}/stop`)).status()).toBe(202);
    });

    test(`04-report${suffix}, 08-evidence${suffix}`, async ({ page, request }) => {
      const id = await submit(page, "What is the current state of grid-scale battery storage?");
      await waitTerminal(request, id);
      await expect(page.locator("#stage-report .prose h2").first()).toBeVisible({ timeout: 20_000 });
      await shoot(page, `04-report${suffix}`);
      await page.locator("#segView button[data-view='evidence']").click();
      await expect(page.locator(".ev-row").first()).toBeVisible();
      await shoot(page, `08-evidence${suffix}`);
    });

    // The report as section cards, reached through the one-time check
    // so its evidence line carries the reader's answers (the phone shortens it to "{date} · {n} sources").
    test(`18-report-cards${suffix}`, async ({ page, request, context }) => {
      await context.setExtraHTTPHeaders({ "X-Replay-Clarify": "on" });
      const id = await submit(page, "What is the current state of grid-scale battery storage?");
      await page.locator("#clarifyCard").getByRole("button", { name: "Just start" }).click({ timeout: 10_000 });
      await waitTerminal(request, id);
      await expect(page.locator("#rep-bottom-line")).toBeVisible({ timeout: 20_000 });
      await page.evaluate(() => window.scrollTo(0, 0));
      await shoot(page, `18-report-cards${suffix}`);
    });

    test(`05-failed${suffix}`, async ({ page, request, context }) => {
      await context.setExtraHTTPHeaders({ "X-Replay-Case": "no-such-case" });
      const id = await submit(page, "What is the current state of grid-scale battery storage?");
      await waitTerminal(request, id);
      await expect(page.locator("#stage-failed")).toBeVisible({ timeout: 20_000 });
      await shoot(page, `05-failed${suffix}`);
    });

    // The one-time check's card, on question 1 of 3.
    test(`10-clarify${suffix}`, async ({ page, request, context }) => {
      await context.setExtraHTTPHeaders({ "X-Replay-Clarify": "on" });
      const id = await submit(page, "What is the current state of grid-scale battery storage?");
      await expect(page.locator("#clarifyStep")).toHaveText("Question 1 of 3", { timeout: 10_000 });
      await shoot(page, `10-clarify${suffix}`);
      await page.locator("#clarifyCard").getByRole("button", { name: "Just start" }).click();
      await waitTerminal(request, id);
    });

    // A note acknowledged at the top of the running Researching row, with the
    // note line at the card's foot; then the report as cards, with no "Your notes" block:
    // replay never applies a note (api-gaps 3.9).
    // 11 is taken from a run held after its second topic completes, for the reason 03's test gives: the
    // acknowledgement arrives through the session's own stream, so the hold does not delay it. 12 needs a
    // run that goes on to its report, so it is a second run.
    test(`11-note-ack${suffix}, 12-report-notes${suffix}`, async ({ page, request, context }) => {
      const addFirstNote = async () => {
        await page.locator('#spine li[data-stage="researcher"][data-state="active"]:not([data-handoff])').waitFor({ timeout: 15_000 });
        await page.getByLabel("Add a note for this research").fill("More on fire-safety standards");
        await page.getByLabel("Add a note for this research").press("Enter");
        await expect(page.locator("#spine .ack", { hasText: "Got it" })).toBeVisible();
      };
      await context.setExtraHTTPHeaders({ "X-Replay-Case": "missing-target-triggers-one-extra-pass", "X-Replay-Hold-After": "researcher.sub_topic.completed#2" });
      const held = await submit(page, "What is the current state of grid-scale battery storage?");
      await addFirstNote();
      await expect(page.locator('#spine li[data-stage="researcher"] .ps-topics [data-topic="done"]')).toHaveCount(2, { timeout: 15_000 });
      // At rest, from the top: typing scrolled the field into view, and its focus ring is not the resting look.
      await page.locator("#noteInput").blur();
      await page.evaluate(() => window.scrollTo(0, 0));
      // Every topic row's mark has settled with its fact, and the subtitle has stopped counting; the subject is the ack.
      await shootSettled(page, `11-note-ack${suffix}`, () => expect(page.locator("#spine .ack", { hasText: "Got it" })).toBeVisible());
      expect((await request.post(`${API}/research/${held}/stop`)).status()).toBe(202);
      await context.setExtraHTTPHeaders({ "X-Replay-Case": "missing-target-triggers-one-extra-pass" });
      const id = await submit(page, "What is the current state of grid-scale battery storage?");
      await addFirstNote();
      const second = await request.post(`${API}/research/${id}/notes`, { data: { text: "Only the United States" } });
      expect(second.status()).toBe(202);
      await waitTerminal(request, id);
      await expect(page.locator("#rep-bottom-line")).toBeVisible({ timeout: 20_000 });
      await expect(page.locator("#stage-report .reader-notes")).toHaveCount(0);
      await page.evaluate(() => window.scrollTo(0, 0));
      await shoot(page, `12-report-notes${suffix}`);
    });

    // The confirmation over the running stage, then the stopped stage.
    // The popover opens as Researching starts; the stop is confirmed the moment a topic is done, so the
    // stopped row carries Researching's facts (about 1.2 s of Researching remain then).
    test(`19-stop-confirm${suffix}, 20-stopped${suffix}`, async ({ page, context }) => {
      await context.setExtraHTTPHeaders({ "X-Replay-Case": "missing-target-triggers-one-extra-pass" });
      await submit(page, "What is the current state of grid-scale battery storage?");
      await page.locator('#spine li[data-stage="researcher"][data-state="active"]').waitFor({ timeout: 15_000 });
      await page.locator("#stopBtn").click();
      await expect(page.getByRole("dialog", { name: "Stop this research?" })).toBeVisible();
      await shoot(page, `19-stop-confirm${suffix}`);
      await page.locator('#spine li[data-stage="researcher"] .ps-topics [data-topic="done"]').first().waitFor({ state: "attached", timeout: 10_000 });
      await page.getByRole("dialog", { name: "Stop this research?" }).getByRole("button", { name: "Stop research" }).click();
      await expect(page.locator("#stage-user-stopped")).toBeVisible();
      await shoot(page, `20-stopped${suffix}`);
    });
  });
}

// At 1920px the report stage holds the contents rail, the
// cards and the Review rail side by side.
test.describe("captures at 1920", () => {
  test.use({ viewport: { width: 1920, height: 1080 } });

  test("18b-report-cards-1920", async ({ page, request }) => {
    const id = await submit(page, "What is the current state of grid-scale battery storage?");
    await waitTerminal(request, id);
    await expect(page.locator('.rep-layout[data-contents="rail"]')).toBeVisible({ timeout: 20_000 });
    await page.evaluate(() => window.scrollTo(0, 0));
    await shoot(page, "18b-report-cards-1920");
  });
});

// Each step's brief, held with X-Replay-Hold-After at a moment
// that shows its body, then stopped (POST /stop). Planning also at phone width.
const BRIEFS = [
  { name: "13-planning-brief", hold: "planner.progress#2", stage: "planner", ready: ".ps-slots > .ln[data-topic='running']" },
  { name: "14-evaluating-brief", hold: "source_evaluator.progress#2", stage: "source_evaluator", ready: ".stats .v" },
  { name: "15-verifying-brief", hold: "evidence_verifier.progress#2", stage: "evidence_verifier", ready: ".tickbox .qt" },
  { name: "16-writing-brief", hold: "report_writer.progress#4", stage: "report_writer", ready: ".tickbox .qt" },
  { name: "17-reviewing-brief", hold: "graph.report.reviewed", stage: "report_reviewer", ready: ".rv-list > .ln[data-topic='done']" },
] as const;
for (const [suffix, viewport, briefs] of [["", null, BRIEFS], ["-phone", PHONE, BRIEFS.slice(0, 1)]] as const) {
  test.describe(`step briefs${suffix}`, () => {
    if (viewport) test.use({ viewport });
    for (const brief of briefs) {
      test(`${brief.name}${suffix}`, async ({ page, request, context }) => {
        await context.setExtraHTTPHeaders({ "X-Replay-Hold-After": brief.hold });
        const id = await submit(page, "What is the current state of grid-scale battery storage?");
        const row = page.locator(`#spine li[data-stage="${brief.stage}"]`);
        await expect(row).toHaveAttribute("data-open", "1", { timeout: 30_000 });
        await expect(row.locator(brief.ready).first()).toBeVisible();
        await page.waitForTimeout(1_500); // the opening row's lines have risen and the ticker has settled
        await shoot(page, `${brief.name}${suffix}`);
        expect((await request.post(`${API}/research/${id}/stop`)).status()).toBe(202);
      });
    }
  });
}
