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

for (const [suffix, viewport] of [["", null], ["-phone", PHONE]] as const) {
  test.describe(`captures${suffix}`, () => {
    if (viewport) test.use({ viewport });

    test(`01-idle${suffix} / 07-idle-phone`, async ({ page }) => {
      await page.goto("/");
      await shoot(page, suffix ? "07-idle-phone" : "01-idle");
    });

    test(`02-submitted${suffix}, 03-running${suffix}, 09-running-extra-pass${suffix}`, async ({ page, request, context }) => {
      // Held after the extra pass's own researcher.sub_topic.started (the 4th: the first pass starts three): at
      // replay pacing the extra pass ends before D39's hold does, so the run would be past Researching and it
      // would never reopen (as in briefs.spec.ts's arc test). Held here, the reopened row shows its topic.
      await context.setExtraHTTPHeaders({ "X-Replay-Case": "missing-target-triggers-one-extra-pass", "X-Replay-Hold-After": "researcher.sub_topic.started#4" });
      const id = await submit(page, "What is the current state of grid-scale battery storage?");
      await expect(page.locator("#stage-submitted")).toBeVisible();
      await shoot(page, `02-submitted${suffix}`);
      // live-briefs spec §6: 03-running is taken with the Researching brief open and a topic done.
      await expect(page.locator('#spine li[data-stage="researcher"][data-open="1"] .ps-topics [data-topic="done"]').first()).toBeVisible({ timeout: 10_000 });
      await shoot(page, `03-running${suffix}`);
      await expect(page.locator('#spineWrap[data-loop="settled"][data-arc="extra_pass"]')).toBeVisible({ timeout: 30_000 });
      // Decision D39: Reviewing holds its verdict for HANDOFF_HOLD_MS first; 09 shows Researching reopened.
      await expect(page.locator('#spine li[data-stage="researcher"][data-open="1"]')).toBeVisible();
      // The row is still opening when data-open flips: its lines rise after the height (spec §6.1), so wait for the reason line.
      await expect(page.locator('#spine li[data-stage="researcher"] .b-why')).toHaveCSS("opacity", "1");
      // The extra pass's one topic, running: the design reference's subject (the reopened row with its topic row).
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

    // notes-progress-report spec §11.3: the report as section cards, reached through the one-time check
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

    // live-briefs spec §6: the one-time check's card, on question 1 of 3 (pick 4B).
    test(`10-clarify${suffix}`, async ({ page, request, context }) => {
      await context.setExtraHTTPHeaders({ "X-Replay-Clarify": "on" });
      const id = await submit(page, "What is the current state of grid-scale battery storage?");
      await expect(page.locator("#clarifyStep")).toHaveText("Question 1 of 3", { timeout: 10_000 });
      await shoot(page, `10-clarify${suffix}`);
      await page.locator("#clarifyCard").getByRole("button", { name: "Just start" }).click();
      await waitTerminal(request, id);
    });

    // live-briefs spec §6: a note acknowledged at the top of the running Researching row, with the
    // note line at the card's foot (pick 6A); then the report as cards, with no "Your notes" block
    // (notes-progress-report spec §7.6): replay never applies a note (api-gaps 3.9).
    test(`11-note-ack${suffix}, 12-report-notes${suffix}`, async ({ page, request, context }) => {
      await context.setExtraHTTPHeaders({ "X-Replay-Case": "missing-target-triggers-one-extra-pass" });
      const id = await submit(page, "What is the current state of grid-scale battery storage?");
      await page.locator('#spine li[data-stage="researcher"][data-state="active"]:not([data-handoff])').waitFor({ timeout: 15_000 });
      await page.getByLabel("Add a note for this research").fill("More on fire-safety standards");
      await page.getByLabel("Add a note for this research").press("Enter");
      await expect(page.locator("#spine .ack", { hasText: "Got it" })).toBeVisible();
      // At rest, from the top: typing scrolled the field into view, and its focus ring is not the resting look.
      await page.locator("#noteInput").blur();
      await page.evaluate(() => window.scrollTo(0, 0));
      await shoot(page, `11-note-ack${suffix}`);
      const second = await request.post(`${API}/research/${id}/notes`, { data: { text: "Only the United States" } });
      expect(second.status()).toBe(202);
      await waitTerminal(request, id);
      await expect(page.locator("#rep-bottom-line")).toBeVisible({ timeout: 20_000 });
      await expect(page.locator("#stage-report .reader-notes")).toHaveCount(0);
      await page.evaluate(() => window.scrollTo(0, 0));
      await shoot(page, `12-report-notes${suffix}`);
    });

    // notes-progress-report spec §11.3: the confirmation over the running stage, then the stopped stage.
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

// notes-progress-report spec §11.3 (D28): at 1920px the report stage holds the contents rail, the
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

// notes-progress-report spec §11.3: each step's brief, held with X-Replay-Hold-After (§6.10) at a moment
// that shows its body, then stopped (Phase D's POST /stop). Planning also at phone width.
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
