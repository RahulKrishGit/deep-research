// Issue 2A — idle→running, three beats (DESIGN.md:1359-1451; prototype clearFlight :1941,
// flyQuestionToLock :2009, holdBeat :2069). Ported across the composer's route (`/`) and the
// destination route (`/research/[id]`) via lib/handoff.ts, since the prototype's single-page
// `.stage` siblings have no route boundary to cross.
import { expect, test, type Page } from "@playwright/test";
import { API } from "./support";

async function submitCountingPosts(page: Page, question: string): Promise<{ id: string; posts: number }> {
  let posts = 0;
  page.on("request", (req) => { if (req.method() === "POST" && /\/api\/research$/.test(req.url())) posts++; });
  await page.goto("/");
  await page.getByLabel("Research question").fill(question);
  await page.getByRole("button", { name: "Start research" }).click();
  await page.waitForURL(/\/research\/[0-9a-f]+$/);
  return { id: page.url().split("/").pop()!, posts };
}

test.describe("idle → running: the question flies from the composer to the locked record", () => {
  test("the .q-flight box exists, is fixed, moves monotonically upward, lands within 2px, and is gone after settle; exactly one POST", async ({ page }) => {
    const { posts } = await submitCountingPosts(page, "How mature is quantum error correction?");
    // Sampled entirely inside the page (no per-sample round trip, nothing that can hang waiting
    // for an element Playwright expects but the animation has already removed): every ~25ms for
    // up to 2s, record the box's rect while it exists, and the first tick it lands within 2px of
    // #submitted-h (measured on the same tick, since the target's own rect never moves) and the
    // first tick it is gone. Long enough to cover the box appearing, the 900ms lift and the
    // 200ms dissolve, however the clear beat's own timing landed relative to navigation.
    const result = await page.evaluate(async () => {
      const samples: Array<{ t: number; top: number; left: number; position: string; ariaHidden: string | null }> = [];
      let convergedAt: number | null = null;
      let removedAt: number | null = null;
      const start = performance.now();
      while (performance.now() - start < 2_000) {
        const t = performance.now() - start;
        const el = document.querySelector(".q-flight") as HTMLElement | null;
        if (el) {
          const r = el.getBoundingClientRect();
          samples.push({ t, top: r.top, left: r.left, position: getComputedStyle(el).position, ariaHidden: el.getAttribute("aria-hidden") });
          const target = document.getElementById("submitted-h");
          const tr = target?.getBoundingClientRect();
          if (convergedAt === null && tr && Math.abs(r.top - tr.top) <= 2 && Math.abs(r.left - tr.left) <= 2) convergedAt = t;
        } else if (samples.length && removedAt === null) {
          removedAt = t;
        }
        const { promise, resolve } = Promise.withResolvers<void>();
        setTimeout(resolve, 25);
        await promise;
      }
      return { samples, convergedAt, removedAt };
    });
    const { samples, convergedAt, removedAt } = result;
    expect(samples.length, "the box must have existed for at least one sample").toBeGreaterThan(0);
    expect(samples.every((s) => s.position === "fixed")).toBe(true);
    expect(samples.every((s) => s.ariaHidden === "true")).toBe(true);
    // Monotonically upward: consecutive samples never move back down by more than rounding noise.
    for (let i = 1; i < samples.length; i++) expect(samples[i].top).toBeLessThanOrEqual(samples[i - 1].top + 1);
    expect(samples[0].top - samples[samples.length - 1].top).toBeGreaterThan(10); // real travel, not jitter
    expect(convergedAt, "must land within 2px of #submitted-h before disappearing").not.toBeNull();
    expect(removedAt, "must be removed after settling, not left in the DOM").not.toBeNull();
    expect(removedAt!).toBeGreaterThanOrEqual(convergedAt!); // never removed while still short of the target
    // The idle page's own composer is gone: this is a different route, not an overlay — one box
    // on screen for the whole journey (DESIGN.md:1407-1422), never a real composer beside it.
    await expect(page.locator("#composer")).toHaveCount(0);
    expect(posts).toBe(1);
  });

  test("a failed POST leaves the composer visible and editable with the error shown, and no stray .q-flight", async ({ page, context }) => {
    await context.route(/\/api\/research$/, (route) => {
      if (route.request().method() !== "POST") return route.continue();
      return route.fulfill({ status: 502, contentType: "application/json", body: JSON.stringify({ error: { code: "api_unreachable", message: "Research service not reachable.", reason: null, issues: [], target: API } }) });
    });
    await page.goto("/");
    const question = page.getByLabel("Research question");
    await question.fill("q");
    await page.getByRole("button", { name: "Start research" }).click();
    await expect(page.getByRole("alert").filter({ hasText: "Research service not reachable at" })).toBeVisible();
    await expect(question).toBeVisible();
    await expect(question).toBeEditable();
    await expect(question).toHaveValue("q");
    await expect(page.locator(".q-flight")).toHaveCount(0);
    await expect(page.locator("#stage-idle")).not.toHaveClass(/is-clearing/);
  });
});

test.describe("abandonment cleanup (review fix round 1, Important #1): the flight box is never stranded", () => {
  test("the first /status 404s (session not in memory): no .q-flight left behind", async ({ page, context }) => {
    let first = true;
    await context.route(/\/api\/research\/[^/]+\/status$/, (route) => {
      if (!first) return route.continue();
      first = false;
      return route.fulfill({ status: 404, contentType: "application/json", body: JSON.stringify({ error: { code: "session_not_found", message: "Session not found.", reason: null, issues: [] } }) });
    });
    await page.goto("/");
    await page.getByLabel("Research question").fill("q");
    await page.getByRole("button", { name: "Start research" }).click();
    await page.waitForURL(/\/research\/[0-9a-f]+$/);
    // exact: true — the sidebar's persistent "New Research" button also matches by substring.
    await expect(page.getByRole("button", { name: "New research", exact: true })).toBeVisible({ timeout: 10_000 });
    await expect(page.locator(".q-flight")).toHaveCount(0);
  });

  test("/status is held past the submitted beat's expiry on every attempt, not just the first: no .q-flight left behind (fix round 2: the prior version never actually went RED)", async ({ page, context }) => {
    // Review fix round 2: holding only the *first* /status let the C1 retry ladder's second
    // attempt (fired ~1000ms after the first, lib/stream.ts's backoffDelaysMs) land inside the
    // 2170ms with-motion beat, so status always arrived on time and this test could never
    // actually observe the bug it names. Holding every matching request past t0+3500ms — t0
    // recorded right at the click, well past the 2170ms budget plus slack for the POST's own
    // latency (the budget is measured from submittedAt, set only once the POST resolves, so a
    // slow POST eats directly into the margin between the click and the beat's real expiry —
    // measured empirically at up to ~400ms here) — guarantees the first successful response
    // lands after the beat has expired, however many attempts the ladder makes first.
    let t0 = 0;
    await context.route(/\/api\/research\/[^/]+\/status$/, async (route) => {
      if (t0) {
        const remaining = 3_500 - (Date.now() - t0);
        if (remaining > 0) { const { promise, resolve } = Promise.withResolvers<void>(); setTimeout(resolve, remaining); await promise; }
      }
      return route.continue();
    });
    await page.goto("/");
    // Installed before navigation and read afterwards: robust against the box appearing and
    // being (or not being) removed before a later point-in-time check would catch it.
    await page.evaluate(() => {
      const w = window as unknown as { __everSeenFlight: boolean };
      w.__everSeenFlight = false;
      new MutationObserver(() => { if (document.querySelector(".q-flight")) w.__everSeenFlight = true; }).observe(document.documentElement, { childList: true, subtree: true });
    });
    await page.getByLabel("Research question").fill("q");
    t0 = Date.now(); // recorded at the click, per the review's own prescription
    await page.getByRole("button", { name: "Start research" }).click();
    await page.waitForURL(/\/research\/[0-9a-f]+$/);
    // Straight to Running, never Submitted: the beat had already elapsed by the time status landed.
    await expect(page.locator("#running-h")).toBeVisible({ timeout: 15_000 });
    await expect(page.locator("#stage-submitted")).toHaveCount(0);
    // The box legitimately exists — it was created on the origin page before navigation, the
    // normal first half of the flight — the bug is that nothing ever removes it once Submitted
    // is skipped. That is what the wait below and the final count actually prove.
    const everSeenFlight = await page.evaluate(() => (window as unknown as { __everSeenFlight: boolean }).__everSeenFlight);
    expect(everSeenFlight, "the box should have existed at some point (created before navigation)").toBe(true);
    await page.waitForTimeout(1_500); // past any lift/dissolve timing a stray box might still be mid-way through
    await expect(page.locator(".q-flight")).toHaveCount(0);
  });
});
