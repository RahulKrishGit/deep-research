import { expect, test } from "@playwright/test";

test("the topbar shows the muted replay-mode chip on every page served by the replay API", async ({ page }) => {
  await page.goto("/");
  await expect(page.locator("#modeChip")).toHaveText("replay mode");
  await page.goto("/research/does-not-exist");
  await expect(page.locator("#modeChip")).toHaveText("replay mode");
});
