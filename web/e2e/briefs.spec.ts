// live-briefs spec §4.3: the step briefs on the real (replay) stream — AC4 (open/close by click and
// keyboard), AC5 (every title, never a bare 0 or a dash), AC6 (connector and arc geometry).
import { expect, test, type Page } from "@playwright/test";
import { API, connectorOffsets, submit, waitTerminal } from "./support";

const ACME_TITLES = ["Adoption rate", "Widget funding", "Widget exports"];
const settledTransitions = (page: Page) => page.waitForFunction(() => document.getAnimations().filter((a) => a instanceof CSSTransition).length === 0);

test("the active row is open; a done row reopens and closes by click, Enter and Space (AC4)", async ({ page, request }) => {
  const id = await submit(page, "q");
  const researcher = page.locator('#spine li[data-stage="researcher"]');
  await expect(researcher).toHaveAttribute("data-state", "active", { timeout: 10_000 });
  await expect(researcher).toHaveAttribute("data-open", "1");
  await expect(researcher).toHaveAttribute("aria-current", "step");
  const planning = page.locator('#spine li[data-stage="planner"]');
  const head = planning.locator("button.ps-toggle");
  await expect(planning).toHaveAttribute("data-open", "0");
  // notes-progress-report spec §6.3: the outcome ends with Planning's duration.
  await expect(planning.locator(".m-out")).toHaveText(/^3 sub-topics · \dm \d\ds$/);
  await expect(head).toHaveAttribute("aria-expanded", "false");
  await head.click();
  await expect(planning).toHaveAttribute("data-open", "1");
  await expect(head).toHaveAttribute("aria-expanded", "true");
  await expect(planning.locator(".ps-slots > .ln:not([data-gone]) .tt")).toHaveText(ACME_TITLES.map((t, i) => `${i + 1}${t}`));
  await head.focus();
  await page.keyboard.press("Enter");
  await expect(planning).toHaveAttribute("data-open", "0");
  await expect(head).toHaveAttribute("aria-expanded", "false");
  await page.keyboard.press("Space");
  await expect(planning).toHaveAttribute("data-open", "1");
  await expect(head).toHaveAttribute("aria-expanded", "true");
  await waitTerminal(request, id);
});

test("the Researching checklist shows every title, and no Researching count ever reads 0 or a dash (AC5)", async ({ page, request }) => {
  const id = await submit(page, "q");
  const researcher = page.locator('#spine li[data-stage="researcher"]');
  await expect(researcher).toHaveAttribute("data-open", "1", { timeout: 10_000 });
  await expect(researcher.locator(".ps-topics .tt")).toHaveText(ACME_TITLES.map((t, i) => `${i + 1}${t}`));
  const seen = new Set<string>();
  for (let i = 0; i < 40; i++) {
    const texts = await researcher.evaluate((li) => [li.querySelector(".m-live")!.textContent!, li.querySelector(".m-out")!.textContent!, ...[...li.querySelectorAll(".ps-topics .tf")].map((f) => f.textContent!)]);
    texts.forEach((t) => seen.add(t));
    if ((await researcher.getAttribute("data-state")) === "done") break;
    await page.waitForTimeout(100);
  }
  for (const text of seen) { expect(text).not.toMatch(/(^|\D)0(\D|$)/); expect(text).not.toContain("—"); }
  await waitTerminal(request, id);
});

for (const [label, viewport] of [["1252×853", { width: 1252, height: 853 }], ["390×844", { width: 390, height: 844 }]] as const) {
  test.describe(label, () => {
    test.use({ viewport });
    test("the connector joins node centres while rows open, close and animate, within 1px (AC6)", async ({ page, request }) => {
      const id = await submit(page, "q");
      await expect(page.locator('#spine li[data-stage="researcher"]')).toHaveAttribute("data-open", "1", { timeout: 10_000 });
      const samples: { pair: string; top: number; bottom: number }[] = [];
      for (let i = 0; i < 60; i++) { samples.push(...(await connectorOffsets(page))); await page.waitForTimeout(50); }
      expect(samples.length).toBeGreaterThan(0);
      for (const s of samples) { expect(Math.abs(s.top), s.pair).toBeLessThanOrEqual(1); expect(Math.abs(s.bottom), s.pair).toBeLessThanOrEqual(1); }
      await waitTerminal(request, id);
    });
  });
}

test("the extra-pass arc stays attached to both nodes after Researching reopens, within 2px (AC6)", async ({ page, request, context }) => {
  await context.setExtraHTTPHeaders({ "X-Replay-Case": "missing-target-triggers-one-extra-pass", "X-Replay-Hold-After": "graph.extra_pass.started" });
  const id = await submit(page, "q");
  await expect(page.locator('#spineWrap[data-arc="extra_pass"]')).toHaveAttribute("data-loop", "settled", { timeout: 30_000 });
  // Decision D39: Reviewing holds its verdict for HANDOFF_HOLD_MS before Researching reopens. The replay's
  // own extra pass (0.9 s of paced events) ends before that hold does, so the run would be past Researching
  // and it would never open: the stream is held after graph.extra_pass.started (and stopped at the end), and
  // the page's own timer hands over to a Researching that is still running.
  await expect(page.locator('#spine li[data-stage="researcher"]')).toHaveAttribute("data-open", "1");
  await settledTransitions(page);
  const gap = await page.evaluate(() => {
    const host = document.getElementById("spineWrap")!.getBoundingClientRect();
    const centre = (stage: string) => { const b = document.querySelector(`#spine li[data-stage="${stage}"] .bullet`)!.getBoundingClientRect(); return b.top - host.top + b.height / 2; };
    const [, , y2, , , , y1] = document.querySelector("#spineWrap .loop-base")!.getAttribute("d")!.split(" ");
    return { leave: Math.abs(Number(y2) - centre("report_reviewer")), enter: Math.abs(Number(y1) - centre("researcher")) };
  });
  expect(gap.leave).toBeLessThanOrEqual(2);
  expect(gap.enter).toBeLessThanOrEqual(2);
  expect((await request.post(`${API}/research/${id}/stop`)).status()).toBe(202);
});
