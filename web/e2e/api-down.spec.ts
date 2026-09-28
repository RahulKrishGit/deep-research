import { expect, test } from "@playwright/test";
import { DEAD_APP, deadPort } from "./support";

test("the banner names the unreachable service; the composer stays usable and never retries a POST", async ({ page }) => {
  const posts: string[] = [];
  page.on("request", (r) => { if (r.method() === "POST" && r.url().endsWith("/api/research")) posts.push(r.url()); });
  await page.goto(`${DEAD_APP}/`);
  const banner = page.getByRole("alert").filter({ hasText: "Research service not reachable at" });
  await expect(banner).toContainText(`Research service not reachable at http://127.0.0.1:${deadPort()}`);
  await expect(banner.getByRole("button", { name: "Retry" })).toBeVisible();
  const box = page.getByLabel("Research question");
  await box.fill("How mature is quantum error correction?");
  await page.getByRole("button", { name: "Start research" }).click();
  await expect(banner).toBeVisible();
  await expect(box).toHaveValue("How mature is quantum error correction?");
  await page.getByRole("button", { name: "Start research" }).click();
  await expect(banner).toBeVisible();
  await expect.poll(() => posts.length).toBe(2); // one POST per click, none of the app's own
  await expect(page).toHaveURL(`${DEAD_APP}/`);
});
