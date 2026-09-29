// live-briefs spec §4.2 (D15, AC8): no extra-passes control anywhere, and the POST body carries no
// max_iterations, so the API applies the configured budget.
import { expect, test } from "@playwright/test";

test("the composer offers no extra-passes control and never sends max_iterations", async ({ page }) => {
  await page.goto("/");
  await expect(page.locator("#pillExtra")).toHaveCount(0);
  await page.locator("#plusBtn").click();
  await expect(page.locator("#settingsPop")).toHaveAttribute("data-open", "true");
  await expect(page.locator("#stepExtra")).toHaveCount(0);
  await expect(page.locator("#settingsPop")).not.toContainText(/extra pass/i);
  await page.locator("#popClose").click();
  await page.getByLabel("Research question").fill("q");
  const posted = page.waitForRequest((r) => r.method() === "POST" && /\/api\/research$/.test(r.url()));
  await page.getByRole("button", { name: "Start research" }).click();
  const body = (await posted).postDataJSON() as Record<string, unknown>;
  expect(Object.keys(body).sort()).toEqual(["config_overrides", "output_format", "query"]);
  await page.waitForURL(/\/research\/[0-9a-f]+$/);
  await expect(page.locator("#runningOpts, #submittedOpts").first()).not.toContainText(/extra pass/i);
});
