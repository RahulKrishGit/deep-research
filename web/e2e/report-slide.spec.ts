// Running → report: the header block slides down (DESIGN.md §5.6, "The two handoffs"; prototype
// REPORT_HANDOFF/enterReport). SessionScreen swaps RunningPipeline for
// ReportStage directly (no route change), so this half of lib/handoff.ts stays a same-tree
// "note, then consume", unlike the idle→running lift's cross-route seam.
import { expect, test, type Page } from "@playwright/test";
import { submit, waitTerminal } from "./support";

/* Records #report-h's own rect for ~400ms from the instant it first appears, entirely inside the
   page — installed before navigation, since the swap can happen faster than a Node round trip
   could react to. */
async function installReportSlideRecorder(page: Page): Promise<void> {
  await page.addInitScript(() => {
    const w = window as unknown as { __slideTrace: Array<{ t: number; top: number }> | null };
    w.__slideTrace = null;
    new MutationObserver((muts) => {
      if (w.__slideTrace) return;
      for (const m of muts) {
        for (const node of Array.from(m.addedNodes)) {
          if (!(node instanceof Element)) continue;
          const h = node.id === "report-h" ? node : node.querySelector("#report-h");
          if (!h || w.__slideTrace) continue;
          const trace: Array<{ t: number; top: number }> = [];
          w.__slideTrace = trace;
          const start = performance.now();
          const tick = () => {
            trace.push({ t: performance.now() - start, top: h.getBoundingClientRect().top });
            if (performance.now() - start < 400) requestAnimationFrame(tick);
          };
          requestAnimationFrame(tick);
        }
      }
    }).observe(document, { childList: true, subtree: true });
  });
}

test("the question frame's top animates from the running position to the report position, strictly through a midpoint", async ({ page, request }) => {
  await installReportSlideRecorder(page);
  const id = await submit(page, "q");
  await expect(page.locator("#running-h")).toBeVisible({ timeout: 10_000 });
  const runningTop = (await page.locator("#running-h").boundingBox())!.y;
  await waitTerminal(request, id);
  await expect(page.locator("#stage-report")).toBeVisible({ timeout: 20_000 });
  await page.waitForTimeout(500); // the recorder's own 400ms window, plus slack
  const trace = await page.evaluate(() => (window as unknown as { __slideTrace: Array<{ t: number; top: number }> | null }).__slideTrace);
  expect(trace, "the recorder must have caught #report-h appear").not.toBeNull();
  const samples = trace!;
  expect(samples.length).toBeGreaterThan(1);
  const restingTop = samples[samples.length - 1].top;
  // The block starts near the running stage's own header position (DESIGN.md: "the block slides
  // down" — the report's head-bar pushes the resting position lower, so restingTop > runningTop).
  expect(restingTop).toBeGreaterThan(runningTop + 10);
  expect(Math.abs(samples[0].top - runningTop)).toBeLessThanOrEqual(10);
  // Strictly between at some sampled point — an actual slide, not a two-frame jump.
  const strictlyBetween = samples.some((s) => s.top > runningTop + 3 && s.top < restingTop - 3);
  expect(strictlyBetween).toBe(true);
  // Monotonically downward (top increasing): no bounce past the resting position and back.
  for (let i = 1; i < samples.length; i++) expect(samples[i].top).toBeGreaterThanOrEqual(samples[i - 1].top - 1);
});

test("opening a finished session from the sidebar does not slide the header", async ({ page, request }) => {
  const id = await submit(page, "q");
  await waitTerminal(request, id);
  await expect(page.locator("#stage-report")).toBeVisible({ timeout: 20_000 }); // this tab's own run finishing does slide — leave that page entirely
  await page.goto("/");
  await expect(page.locator(`.sb-item[data-session="${id}"]`)).toBeVisible({ timeout: 10_000 });
  await installReportSlideRecorder(page);
  await page.locator(`.sb-item[data-session="${id}"]`).click();
  await expect(page.locator("#stage-report")).toBeVisible({ timeout: 20_000 });
  await page.waitForTimeout(500);
  const el = page.locator("#report-h");
  const style = await el.evaluate((e) => ({ transform: (e as HTMLElement).style.transform, computed: getComputedStyle(e).transform }));
  expect(style.transform).toBe(""); // never given an inline offset to release
  expect(["none", "matrix(1, 0, 0, 1, 0, 0)"]).toContain(style.computed); // never mid-transition either
});

test("a session watched running earlier, then reached again via the sidebar after opening a different finished session in between, does not slide", async ({ page, request }) => {
  const s0 = await submit(page, "s0 question");
  await waitTerminal(request, s0);
  await expect(page.locator("#stage-report")).toBeVisible({ timeout: 20_000 }); // s0: the older, already-finished session

  const s1 = await submit(page, "s1 question");
  await expect(page.locator("#running-h")).toBeVisible({ timeout: 10_000 }); // watch s1 run: noteRunningLayout fires for s1

  await page.locator(`.sb-item[data-session="${s0}"]`).click(); // open the older finished session in between
  await expect(page.locator("#stage-report")).toBeVisible({ timeout: 10_000 });

  await waitTerminal(request, s1); // s1 finishes in the background while s0 is on screen

  await page.locator(`.sb-item[data-session="${s1}"]`).click(); // back to s1, now finished, via the sidebar
  await expect(page.locator("#stage-report")).toBeVisible({ timeout: 20_000 });
  await page.waitForTimeout(500);
  const el = page.locator("#report-h");
  const style = await el.evaluate((e) => (e as HTMLElement).style.transform);
  expect(style).toBe("");
});
