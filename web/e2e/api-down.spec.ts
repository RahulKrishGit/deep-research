import { expect, test } from "@playwright/test";
import { DEAD_APP, deadPort } from "./support";

test("the banner names the unreachable service; the composer stays usable and never retries a POST", async ({ page }) => {
  const posts: string[] = [];
  page.on("request", (r) => { if (r.method() === "POST" && r.url().endsWith("/api/research")) posts.push(r.url()); });
  // Stub the load-time sidebar list: ConsoleProvider's own GET on mount
  // would otherwise raise the banner before any submit, making every assertion below vacuous —
  // after this, only a POST can raise it.
  await page.route(/\/api\/research\?limit=/, (r) => r.fulfill({ json: { sessions: [] } }));
  await page.goto(`${DEAD_APP}/`);
  const banner = page.getByRole("alert").filter({ hasText: "Research service not reachable at" });
  await expect(banner).toBeHidden();
  const box = page.getByLabel("Research question");
  const sendBtn = page.getByRole("button", { name: "Start research" });
  await box.fill("How mature is quantum error correction?");

  // Each click is awaited against its own POST response (not an instant-pass assertion) so the
  // second click can never land while the first request is still in flight — the send button
  // isn't disabled while busy, so an un-synchronised second click would race the first.
  await Promise.all([
    page.waitForResponse((r) => r.request().method() === "POST" && r.url().endsWith("/api/research")),
    sendBtn.click(),
  ]);
  await expect(banner).toContainText(`Research service not reachable at http://127.0.0.1:${deadPort()}`);
  await expect(banner.getByRole("button", { name: "Retry" })).toBeVisible();
  await expect(box).toHaveValue("How mature is quantum error correction?");

  await Promise.all([
    page.waitForResponse((r) => r.request().method() === "POST" && r.url().endsWith("/api/research")),
    sendBtn.click(),
  ]);
  await expect(banner).toContainText(`Research service not reachable at http://127.0.0.1:${deadPort()}`);
  await expect(banner.getByRole("button", { name: "Retry" })).toBeVisible();
  await expect(page).toHaveURL(`${DEAD_APP}/`);

  // A late, unwanted third POST would land after the two clicks above; give it a second to show
  // up before the final count, so `posts` can only be exactly 2 — not "at least 2 so far".
  await page.waitForTimeout(1000);
  expect(posts).toHaveLength(2);
});
