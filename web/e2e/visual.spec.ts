import { mkdirSync } from "node:fs";
import path from "node:path";
import { expect, test, type Page } from "@playwright/test";
import { submit, waitTerminal } from "./support";

const CHECKPOINT = process.env.VISUAL_CHECKPOINT ?? "C4";
const dir = path.join("visual", CHECKPOINT);
mkdirSync(dir, { recursive: true });
const shoot = async (page: Page, name: string) => {
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
      await context.setExtraHTTPHeaders({ "X-Replay-Case": "missing-target-triggers-one-extra-pass" });
      const id = await submit(page, "What is the current state of grid-scale battery storage?");
      await expect(page.locator("#stage-submitted")).toBeVisible();
      await shoot(page, `02-submitted${suffix}`);
      await expect(page.locator("#runNow")).toHaveText("Researching", { timeout: 10_000 });
      await shoot(page, `03-running${suffix}`);
      await expect(page.locator('#spineWrap[data-loop="settled"][data-arc="extra_pass"]')).toBeVisible({ timeout: 30_000 });
      await shoot(page, `09-running-extra-pass${suffix}`);
      await waitTerminal(request, id);
    });
  });
}
