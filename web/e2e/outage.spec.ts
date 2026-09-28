// C1: the banner used to hold a single { target, retry } pair, shared across every component that
// could raise it — whichever component noted it *last* silently owned the slot, any success
// cleared it for everyone, and SessionScreen had no automatic ladder before its first successful
// `/status`. A page loaded while the API was down could sit on "loading session" forever, or clear
// to a false "not reachable" banner that never actually recovered. These specs reproduce the outage
// with `page.route` 502 stubs — no real process kill needed — and prove the fixed, keyed registry:
// the banner is up while any owner's key is registered, and Retry re-runs every registered retry.
import { expect, test, type Page, type Route } from "@playwright/test";
import { submit, waitTerminal } from "./support";

const UNREACHABLE_TARGET = "http://127.0.0.1:8010";
const unreachableBody = { error: { code: "api_unreachable", message: "Research service not reachable.", reason: null, issues: [], target: UNREACHABLE_TARGET } };
const fulfill502 = (route: Route) => route.fulfill({ status: 502, contentType: "application/json", body: JSON.stringify(unreachableBody) });
const banner = (page: Page) => page.getByRole("alert").filter({ hasText: "Research service not reachable at" });
// Real delay, not a fake timer: used only to race two genuine browser network requests against
// each other (see the (c) tests below) — there is no code path here a fake clock could drive.
function delay(ms: number): Promise<void> {
  const { promise, resolve } = Promise.withResolvers<void>();
  setTimeout(resolve, ms);
  return promise;
}

test.describe("outage recovery (C1)", () => {
  test("(a) a session page loaded while /status is down recovers on its own, with no click, once the service answers", async ({ page, request }) => {
    const id = await submit(page, "q");
    await waitTerminal(request, id); // terminal before we ever navigate: the outage is only in the read, not the run itself
    let statusDown = true;
    await page.route(new RegExp(`/api/research/${id}/status`), (route) => (statusDown ? fulfill502(route) : route.continue()));
    await page.goto(`/research/${id}`);
    await expect(page.locator("#stage-loading")).toBeVisible();
    await expect(banner(page)).toBeVisible();
    statusDown = false; // the service "comes back"; nothing in this test ever clicks anything
    await expect(page.locator("#stage-report")).toBeVisible({ timeout: 20_000 }); // the ladder's own retry gets there
    await expect(page.locator("#stage-loading")).toBeHidden();
    await expect(banner(page)).toBeHidden();
  });

  test("(b) Retry recovers the same outage immediately, without waiting for the ladder", async ({ page, request }) => {
    const id = await submit(page, "q");
    await waitTerminal(request, id);
    let statusDown = true;
    await page.route(new RegExp(`/api/research/${id}/status`), (route) => (statusDown ? fulfill502(route) : route.continue()));
    await page.goto(`/research/${id}`);
    await expect(banner(page)).toBeVisible();
    statusDown = false;
    await banner(page).getByRole("button", { name: "Retry" }).click();
    await expect(page.locator("#stage-report")).toBeVisible({ timeout: 5_000 });
    await expect(banner(page)).toBeHidden();
  });

  test("(c) sidebar registers last: after Retry both the sidebar count and the session stage render, and the banner clears", async ({ page, request }) => {
    const id = await submit(page, "q");
    await waitTerminal(request, id);
    let down = true;
    // Real delay, not a fake timer: this races two genuine browser network requests against each
    // other so the sidebar's list read settles (and so registers its own key) after the session's
    // own status read — deterministic ordering is the only way to prove *both* registration orders
    // (this test and (c)'s sibling below) recover the same way.
    await page.route(/\/api\/research\?limit=/, async (route) => {
      if (!down) return route.continue();
      await delay(200);
      return fulfill502(route);
    });
    await page.route(new RegExp(`/api/research/${id}/status`), (route) => (down ? fulfill502(route) : route.continue()));
    await page.goto(`/research/${id}`);
    await expect(banner(page)).toBeVisible();
    down = false;
    await banner(page).getByRole("button", { name: "Retry" }).click();
    await expect(page.locator("#stage-report")).toBeVisible({ timeout: 5_000 });
    await expect(page.locator("#sbCount")).toHaveText(/^\d+$/);
    await expect(banner(page)).toBeHidden();
  });

  test("(c) session registers last: after Retry both the sidebar count and the session stage render, and the banner clears", async ({ page, request }) => {
    const id = await submit(page, "q");
    await waitTerminal(request, id);
    let down = true;
    await page.route(/\/api\/research\?limit=/, (route) => (down ? fulfill502(route) : route.continue()));
    await page.route(new RegExp(`/api/research/${id}/status`), async (route) => {
      if (!down) return route.continue();
      await delay(200); // see the sibling test above for why this is a real delay
      return fulfill502(route);
    });
    await page.goto(`/research/${id}`);
    await expect(banner(page)).toBeVisible();
    down = false;
    await banner(page).getByRole("button", { name: "Retry" }).click();
    await expect(page.locator("#stage-report")).toBeVisible({ timeout: 5_000 });
    await expect(page.locator("#sbCount")).toHaveText(/^\d+$/);
    await expect(banner(page)).toBeHidden();
  });

  test("(d) /status answers 502 then 404: the not-in-memory sentence renders, with no banner left up", async ({ page }) => {
    let calls = 0;
    await page.route(/\/api\/research\/outage-spec-unknown\/status/, (route) => {
      calls++;
      if (calls === 1) return fulfill502(route);
      return route.fulfill({ status: 404, contentType: "application/json", body: JSON.stringify({ error: { code: "session_not_found", message: "Unknown session.", reason: null, issues: [] } }) });
    });
    await page.goto("/research/outage-spec-unknown");
    await expect(banner(page)).toBeVisible();
    await expect(page.getByText("This session isn't in the service's memory — sessions are lost when the API restarts.")).toBeVisible({ timeout: 15_000 });
    await expect(banner(page)).toBeHidden();
  });
});
