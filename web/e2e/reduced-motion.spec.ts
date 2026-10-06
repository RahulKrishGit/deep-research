// Reduced motion (DESIGN.md §5.6, "The two handoffs"): no flight box travel, the
// stages still arrive. Both handoffs' journeys are skipped under prefers-reduced-motion — no
// .q-flight box is ever created, no header-slide transform is ever applied — while the ordinary
// stage-change cross-fade (already handled by the stylesheet's own reduced-motion block in
// globals.css) still runs, so Submitted, Running and Report still each arrive.
//
// A single point-in-time check is not enough here: the CSS reduced-motion block zeroes the
// .q-flight box's own transition-duration regardless of the JS gate this proves, so a box created
// without the gate would still exist in the DOM for its full (JS-timed) lift+dissolve life —
// visually static, but present — and a single "does #running-h exist yet" check can land either
// well before or well after that window. These sample continuously instead.
import { expect, test } from "@playwright/test";
import { submit, waitTerminal } from "./support";

test.use({ reducedMotion: "reduce" });

test("idle → running under reduced motion: no .q-flight box ever exists, and the stages still arrive", async ({ page }) => {
  await page.goto("/");
  await page.evaluate(() => {
    const w = window as unknown as { __everSeenFlight: boolean };
    w.__everSeenFlight = false;
    new MutationObserver(() => {
      if (document.querySelector(".q-flight")) w.__everSeenFlight = true;
    }).observe(document.documentElement, { childList: true, subtree: true });
  });
  await page.getByLabel("Research question").fill("How mature is quantum error correction?");
  await page.getByRole("button", { name: "Start research" }).click();
  await page.waitForURL(/\/research\/[0-9a-f]+$/);
  await expect(page.locator("#running-h")).toBeVisible({ timeout: 10_000 }); // the stage still arrives
  const everSeenFlight = await page.evaluate(() => (window as unknown as { __everSeenFlight: boolean }).__everSeenFlight);
  expect(everSeenFlight).toBe(false);
  await expect(page.locator(".q-flight")).toHaveCount(0);
});

test("running → report under reduced motion: #report-h never gets a slide transition, and the stage still arrives", async ({ page, request }) => {
  // A `style.transform === ""` check at the end is not enough here: runReportSlide always
  // releases the transform back to "" synchronously within the same call regardless of the
  // gate, and the CSS reduced-motion block zeroes the transition-duration either way — so the
  // one thing that distinguishes "the gate skipped this" from "it ran and finished" is whether
  // `style.transition` was ever set at all, captured the instant #report-h is inserted (before
  // the un-gated path's own 240ms cleanup timer would clear it back to "").
  await page.addInitScript(() => {
    const w = window as unknown as { __reportHTransitionAtAppear: string | null };
    w.__reportHTransitionAtAppear = null;
    new MutationObserver((muts) => {
      if (w.__reportHTransitionAtAppear !== null) return;
      for (const m of muts) {
        for (const node of Array.from(m.addedNodes)) {
          if (!(node instanceof Element)) continue;
          const h = node.id === "report-h" ? (node as HTMLElement) : node.querySelector<HTMLElement>("#report-h");
          if (h && w.__reportHTransitionAtAppear === null) w.__reportHTransitionAtAppear = h.style.transition;
        }
      }
    }).observe(document, { childList: true, subtree: true });
  });
  const id = await submit(page, "q");
  await expect(page.locator("#running-h")).toBeVisible({ timeout: 10_000 });
  await waitTerminal(request, id);
  await expect(page.locator("#stage-report")).toBeVisible({ timeout: 20_000 }); // the stage still arrives
  const transitionAtAppear = await page.evaluate(() => (window as unknown as { __reportHTransitionAtAppear: string | null }).__reportHTransitionAtAppear);
  expect(transitionAtAppear).toBe("");
  const style = await page.locator("#report-h").evaluate((e) => (e as HTMLElement).style.transform);
  expect(style).toBe("");
});
