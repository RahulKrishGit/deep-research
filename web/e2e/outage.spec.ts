// Outage recovery: the unreachable-API banner is a keyed registry shared by every component that
// can raise it. A page loaded while the API was down must not sit on "loading session" forever,
// or clear to a false "not reachable" banner that never actually recovered. These specs reproduce
// the outage with `page.route` 502 stubs — no real process kill needed — and prove the registry:
// the banner is up while any owner's key is registered, Retry re-runs every registered retry, and
// any owner's own success cascades one retry to every other owner still registered, so the banner
// disappears on the first success, not only once every owner has succeeded independently.
//
// A real outage fails the sidebar's list read too, not only the session's own
// read — (a) and (d) below stub both. (c)'s two ordering variants synchronise on each route
// handler's own fulfilment (not a guessed wall-clock wait) before ever touching Retry, and hold
// the banner-hidden assertion for a further 2 s to catch a late re-registration the click raced.
import { expect, test, type Page, type Route } from "@playwright/test";
import { submit, waitTerminal } from "./support";

const UNREACHABLE_TARGET = "http://127.0.0.1:8010";
const unreachableBody = { error: { code: "api_unreachable", message: "Research service not reachable.", reason: null, issues: [], target: UNREACHABLE_TARGET } };
const fulfill502 = (route: Route) => route.fulfill({ status: 502, contentType: "application/json", body: JSON.stringify(unreachableBody) });
const banner = (page: Page) => page.getByRole("alert").filter({ hasText: "Research service not reachable at" });
// Real delay, not a fake timer: used only to bias two genuine browser network requests to land in
// a chosen order (see the (c) tests below) — there is no code path here a fake clock could drive.
function delay(ms: number): Promise<void> {
  const { promise, resolve } = Promise.withResolvers<void>();
  setTimeout(resolve, ms);
  return promise;
}

test.describe("outage recovery", () => {
  test("(a) a session page loaded while /status AND the list read are both down recovers on its own, with no click", async ({ page, request }) => {
    const id = await submit(page, "q");
    await waitTerminal(request, id); // terminal before we ever navigate: the outage is only in the reads, not the run itself
    let down = true;
    await page.route(new RegExp(`/api/research/${id}/status`), (route) => (down ? fulfill502(route) : route.continue()));
    await page.route(/\/api\/research\?limit=/, (route) => (down ? fulfill502(route) : route.continue()));
    await page.goto(`/research/${id}`);
    await expect(page.locator("#stage-loading")).toBeVisible();
    await expect(banner(page)).toBeVisible();
    down = false; // the service "comes back"; nothing in this test ever clicks anything
    await expect(page.locator("#stage-report")).toBeVisible({ timeout: 20_000 }); // the ladder's own retry gets there
    await expect(page.locator("#stage-loading")).toBeHidden();
    // The session key's own ladder success must cascade to the sidebar's key, which has no
    // automatic retry of its own — the count has to load without a click too.
    await expect(page.locator("#sbCount")).toHaveText(/^\d+$/);
    await expect(banner(page)).toBeHidden();
  });

  test("(b) Retry recovers the same outage immediately, without waiting for the ladder", async ({ page, request }) => {
    const id = await submit(page, "q");
    await waitTerminal(request, id);
    let down = true;
    await page.route(new RegExp(`/api/research/${id}/status`), (route) => (down ? fulfill502(route) : route.continue()));
    await page.route(/\/api\/research\?limit=/, (route) => (down ? fulfill502(route) : route.continue()));
    await page.goto(`/research/${id}`);
    await expect(banner(page)).toBeVisible();
    down = false;
    await banner(page).getByRole("button", { name: "Retry" }).click();
    await expect(page.locator("#stage-report")).toBeVisible({ timeout: 5_000 });
    await expect(page.locator("#sbCount")).toHaveText(/^\d+$/);
    await expect(banner(page)).toBeHidden();
  });

  test("(e) a running session recovers as soon as the stream resumes, banner included", async ({ page, context }) => {
    await context.setExtraHTTPHeaders({ "X-Replay-Case": "missing-target-triggers-one-extra-pass" }); // paced: still running when we reload into the outage
    const id = await submit(page, "q");
    await expect(page.locator("#stage-running")).toBeVisible({ timeout: 5_000 });
    let down = true;
    await page.route(new RegExp(`/api/research/${id}/status`), (route) => (down ? fulfill502(route) : route.continue()));
    await page.route(/\/api\/research\?limit=/, (route) => (down ? fulfill502(route) : route.continue()));
    await page.route(new RegExp(`/api/research/${id}/stream`), (route) => (down ? fulfill502(route) : route.continue()));
    await page.reload();
    await expect(banner(page)).toBeVisible();
    down = false;
    await expect(page.locator("#stage-running")).toBeVisible({ timeout: 20_000 });
    // The banner must not sit over a live run for the rest of its duration — it has to clear as
    // soon as the stream (and, by cascade, the sidebar) resume, not only once the run finishes.
    await expect(banner(page)).toBeHidden({ timeout: 20_000 });
  });

  test("(c) sidebar's read lands last: once both reads have genuinely landed, Retry recovers and stays recovered", async ({ page, request }) => {
    const id = await submit(page, "q");
    await waitTerminal(request, id);
    let down = true;
    const { promise: statusLanded, resolve: resolveStatus } = Promise.withResolvers<void>();
    const { promise: listLanded, resolve: resolveList } = Promise.withResolvers<void>();
    await page.route(new RegExp(`/api/research/${id}/status`), async (route) => {
      if (!down) return route.continue();
      await fulfill502(route);
      resolveStatus();
    });
    await page.route(/\/api\/research\?limit=/, async (route) => {
      if (!down) return route.continue();
      await delay(200); // biases real request timing so the list genuinely fulfils after status
      await fulfill502(route);
      resolveList();
    });
    await page.goto(`/research/${id}`);
    // Both reads have actually landed, in this order, before Retry is ever touched — a guessed
    // wall-clock wait here let a previous version of this test click before the delayed 502 had
    // landed, so the sidebar key was never registered when Retry ran (it "passed" for the wrong
    // reason: the 1 s ladder, not Retry, eventually cleared the banner).
    await statusLanded;
    await listLanded;
    down = false;
    await banner(page).getByRole("button", { name: "Retry" }).click();
    await expect(page.locator("#stage-report")).toBeVisible({ timeout: 5_000 });
    await expect(page.locator("#sbCount")).toHaveText(/^\d+$/);
    await expect(banner(page)).toBeHidden();
    // Hold well past both reads to catch a banner that flickers back up from a late race.
    await page.waitForTimeout(2_000);
    await expect(banner(page)).toBeHidden();
  });

  test("(c) session's read lands last: once both reads have genuinely landed, Retry recovers and stays recovered", async ({ page, request }) => {
    const id = await submit(page, "q");
    await waitTerminal(request, id);
    let down = true;
    const { promise: statusLanded, resolve: resolveStatus } = Promise.withResolvers<void>();
    const { promise: listLanded, resolve: resolveList } = Promise.withResolvers<void>();
    await page.route(new RegExp(`/api/research/${id}/status`), async (route) => {
      if (!down) return route.continue();
      await delay(200); // see the sibling test above for why this is a real delay
      await fulfill502(route);
      resolveStatus();
    });
    await page.route(/\/api\/research\?limit=/, async (route) => {
      if (!down) return route.continue();
      await fulfill502(route);
      resolveList();
    });
    await page.goto(`/research/${id}`);
    await listLanded;
    await statusLanded;
    down = false;
    await banner(page).getByRole("button", { name: "Retry" }).click();
    await expect(page.locator("#stage-report")).toBeVisible({ timeout: 5_000 });
    await expect(page.locator("#sbCount")).toHaveText(/^\d+$/);
    await expect(banner(page)).toBeHidden();
    await page.waitForTimeout(2_000);
    await expect(banner(page)).toBeHidden();
  });

  test("(d) /status answers 502 then 404, with the list read down too: the not-in-memory sentence renders, with no banner left up", async ({ page }) => {
    let calls = 0;
    let listDown = true;
    await page.route(/\/api\/research\?limit=/, (route) => (listDown ? fulfill502(route) : route.continue()));
    await page.route(/\/api\/research\/outage-spec-unknown\/status/, (route) => {
      calls++;
      if (calls === 1) return fulfill502(route);
      listDown = false; // the service is back by the time the session read lands its definitive 404
      return route.fulfill({ status: 404, contentType: "application/json", body: JSON.stringify({ error: { code: "session_not_found", message: "Unknown session.", reason: null, issues: [] } }) });
    });
    await page.goto("/research/outage-spec-unknown");
    await expect(banner(page)).toBeVisible();
    await expect(page.getByText("This session isn't in the service's memory — sessions are lost when the API restarts.")).toBeVisible({ timeout: 15_000 });
    // The 404 clears the session's own key, and cascades a retry to the sidebar's
    // key too — the banner must not linger just because the sidebar's own read has no ladder.
    await expect(page.locator("#sbCount")).toHaveText(/^\d+$/);
    await expect(banner(page)).toBeHidden();
  });
});
