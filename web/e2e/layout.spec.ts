import { expect, test, type Page } from "@playwright/test";
import { submit, waitTerminal } from "./support";

const px = (page: Page, sel: string, prop: "width" | "height") => page.locator(sel).first().evaluate((el, p) => el.getBoundingClientRect()[p as "width" | "height"], prop);
const noSideScroll = async (page: Page) => expect(await page.evaluate(() => document.scrollingElement!.scrollWidth <= window.innerWidth)).toBe(true);
const cardsUnclipped = async (page: Page) => {
  const clipped = await page.locator(".card").evaluateAll((els) => els.filter((e) => e.scrollHeight > e.clientHeight + 1 && getComputedStyle(e).overflowY !== "auto").length);
  expect(clipped).toBe(0);
};

for (const [label, viewport] of [["1252×853", { width: 1252, height: 853 }], ["390×844", { width: 390, height: 844 }]] as const) {
  test.describe(label, () => {
    // reducedMotion: the .app grid animates its columns over --motion-base (index.html:78-81); the CSS
    // zeroes transitions under prefers-reduced-motion (index.html:997-1000), so widths are final at once.
    test.use({ viewport, reducedMotion: "reduce" });
    const phone = viewport.width === 390;

    test("idle", async ({ page }) => {
      await page.goto("/");
      await noSideScroll(page);
      if (phone) {
        await expect(page.locator("#app")).toHaveAttribute("data-sidebar", "collapsed");
      } else {
        await expect.poll(() => px(page, "#sidebar", "width")).toBe(296);
        expect(await px(page, ".topbar-in", "height")).toBe(56);
        await page.locator("#sidebarToggle").click();
        await expect.poll(() => px(page, "#sidebar", "width")).toBe(64);
        await page.locator("#sidebarToggle").click();
        await expect.poll(() => px(page, "#sidebar", "width")).toBe(296);
      }
    });

    test("running, report, evidence, failed, not found", async ({ page, request, context }) => {
      await context.setExtraHTTPHeaders({ "X-Replay-Case": "extra-pass-finds-nothing" });
      const id = await submit(page, "q");
      await expect(page.locator("#stage-running")).toBeVisible({ timeout: 5_000 });
      await noSideScroll(page);
      await expect(page.locator("#spine li[data-stage]")).toHaveCount(7);
      expect(await page.locator("#spine li[data-stage]").evaluateAll((els) => els.map((e) => e.getAttribute("data-stage")))).toEqual(["planner", "researcher", "source_evaluator", "evidence_verifier", "report_writer", "report_reviewer", "finalize_report"]);
      await cardsUnclipped(page);
      await waitTerminal(request, id);
      await expect(page.locator("#stage-report .prose h2").first()).toBeVisible({ timeout: 20_000 });
      await noSideScroll(page);
      await cardsUnclipped(page);
      if (!phone) {
        expect(await px(page, ".prose", "width")).toBeLessThanOrEqual(720);
        expect(await px(page, ".rail", "width")).toBe(300);
      } else {
        // Final-wave item 1: at 390 px, with the replay mode chip showing and the status chip's
        // note at its longest (a completed, reviewed run), the topbar must still hold both chips
        // on its one fixed-height row rather than wrapping the mode chip onto a second line. 60,
        // not the desktop's 56: globals.css:987 enlarges .icon-btn to a 44 px touch target at this
        // breakpoint (verbatim prototype CSS, phone-only), so 60 = 44 + the topbar-in's own 8 px
        // top/bottom padding is this row's real single-line height — a second row would add a
        // whole chip's height (~28 px) on top of that, not 4 px.
        expect(await px(page, ".topbar-in", "height")).toBe(60);
        const modeBox = await page.locator("#modeChip").boundingBox();
        const statusBox = await page.locator("#topbarStatus .chip").boundingBox();
        expect(modeBox).not.toBeNull();
        expect(statusBox).not.toBeNull();
        expect(modeBox!.y).toBeLessThan(statusBox!.y + statusBox!.height);
        expect(statusBox!.y).toBeLessThan(modeBox!.y + modeBox!.height);
      }
      await page.locator("#segView button[data-view='evidence']").click();
      await expect(page.locator(".ev-row").first()).toBeVisible();
      await noSideScroll(page);
      await page.goto("/research/does-not-exist");
      // exact: true — the sidebar's persistent "New Research" button (Sidebar.tsx) matches the
      // default case-insensitive substring name search too; only the case differs from this one
      // (same ambiguity as not-found.spec.ts, same accepted fix).
      await expect(page.getByRole("button", { name: "New research", exact: true })).toBeVisible();
      await noSideScroll(page);
      await context.setExtraHTTPHeaders({ "X-Replay-Case": "no-such-case" });
      const failedId = await submit(page, "q");
      await waitTerminal(request, failedId);
      await expect(page.locator("#stage-failed")).toBeVisible({ timeout: 20_000 });
      await noSideScroll(page);
      await cardsUnclipped(page);
    });

    // Controller ruling: a full-page capture at this 853px viewport shows the sticky 100vh sidebar
    // and the sticky, internally-scrolling rail cut off once the page's content exceeds the viewport
    // height — that is a screenshot artifact of `fullPage: true`, not a layout defect. This proves the
    // live layout instead: once the page is scrolled to its end, the sidebar still covers the full
    // viewport (it is pinned to the viewport, not the document), and the rail — shorter than the report
    // but taller than the viewport (globals.css:314-322) — either fits without scrolling or exposes its
    // last card through its own internal scroll, so every rail card stays reachable.
    if (!phone) {
      test("report stage: the sticky sidebar and rail stay reachable once the page outgrows the viewport", async ({ page, request }) => {
        const id = await submit(page, "q");
        await waitTerminal(request, id);
        await expect(page.locator("#stage-report .prose h2").first()).toBeVisible({ timeout: 20_000 });
        await page.evaluate(() => window.scrollTo(0, document.body.scrollHeight));

        const innerHeight = await page.evaluate(() => window.innerHeight);
        const sidebarBox = await page.locator(".sidebar").evaluate((el) => {
          const r = el.getBoundingClientRect();
          return { top: r.top, bottom: r.bottom };
        });
        expect(sidebarBox.top).toBeLessThanOrEqual(0);
        expect(sidebarBox.bottom).toBeGreaterThanOrEqual(innerHeight - 1);

        const rail = page.locator(".rail");
        const before = await rail.evaluate((el) => ({ scrollHeight: el.scrollHeight, clientHeight: el.clientHeight }));
        if (before.scrollHeight > before.clientHeight + 1) {
          // Not fully visible at once: prove it is genuinely scrollable, not merely clipped —
          // scrolling it to its end must bring the last card to (or above) its own bottom edge.
          await rail.evaluate((el) => { el.scrollTop = el.scrollHeight; });
          const railBottom = await rail.evaluate((el) => el.getBoundingClientRect().bottom);
          const lastCardBottom = await page.locator(".rail .card").last().evaluate((el) => el.getBoundingClientRect().bottom);
          expect(lastCardBottom).toBeLessThanOrEqual(railBottom + 1);
        } else {
          // Fully visible: every card already sits inside the viewport with no scroll needed.
          const lastCardBottom = await page.locator(".rail .card").last().evaluate((el) => el.getBoundingClientRect().bottom);
          expect(lastCardBottom).toBeLessThanOrEqual(innerHeight + 1);
        }
      });
    }
  });
}
