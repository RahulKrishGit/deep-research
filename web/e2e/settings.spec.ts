// live-briefs spec §4.2 (D15, AC8): no extra-passes control anywhere, and the POST body carries no
// max_iterations, so the API applies the configured budget.
import { expect, test } from "@playwright/test";
import { API, waitTerminal } from "./support";

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
  expect(Object.keys(body).sort()).toEqual(["ask_clarifying_questions", "config_overrides", "output_format", "query"]);
  expect(body.ask_clarifying_questions).toBe(true);
  await page.waitForURL(/\/research\/[0-9a-f]+$/);
  await expect(page.locator("#runningOpts, #submittedOpts").first()).not.toContainText(/extra pass/i);
});

// live-briefs spec D16, AC13: with the setting off the session makes no check at all — even with
// X-Replay-Clarify: on, which makes replay's scripted checker ask whenever it is called.
test("with 'Ask me when the question is unclear' off, no check is made (AC13)", async ({ page, context, request }) => {
  await context.setExtraHTTPHeaders({ "X-Replay-Clarify": "on" });
  await page.goto("/");
  await page.locator("#plusBtn").click();
  // The row sits in the slot the extra-passes stepper left, and the panel still shows all of it at 1252×853.
  await expect(page.locator("#segAsk")).toBeInViewport({ ratio: 1 });
  await expect(page.locator('#segAsk button[data-ask="on"]')).toHaveAttribute("aria-pressed", "true");
  await page.locator('#segAsk button[data-ask="off"]').click();
  await expect(page.locator('#segAsk button[data-ask="off"]')).toHaveAttribute("aria-pressed", "true");
  await page.locator("#popClose").click();
  await page.getByLabel("Research question").fill("q");
  const posted = page.waitForRequest((r) => r.method() === "POST" && /\/api\/research$/.test(r.url()));
  await page.getByRole("button", { name: "Start research" }).click();
  expect(((await posted).postDataJSON() as Record<string, unknown>).ask_clarifying_questions).toBe(false);
  await page.waitForURL(/\/research\/[0-9a-f]+$/);
  const id = page.url().split("/").pop()!;
  await expect(page.locator("#stage-running")).toBeVisible({ timeout: 10_000 });
  await expect(page.locator("#stage-clarify")).toHaveCount(0);
  await waitTerminal(request, id);
  expect(await (await request.get(`${API}/research/${id}/stream`)).text()).not.toContain("session.clarification");
});
