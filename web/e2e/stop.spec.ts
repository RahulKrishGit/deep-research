// notes-progress-report spec §8 (D17, D18, D24, D29, D33; AC28, AC30, AC31, AC33): Stop on the replay
// server. Replay paces the stream (150 ms an event) while the engine runs ahead unpaced, so the page's
// active row is the stream's — and the step a stop records is read from that same stream (AC32).
import { expect, test, type Page } from "@playwright/test";
import { API, submit, waitTerminal } from "./support";

const QUESTION = "What is the current state of grid-scale battery storage?";
const chip = (page: Page) => page.locator("#topbarStatus .chip");
const stop = (page: Page) => page.locator("#stopBtn");
const popover = (page: Page) => page.getByRole("dialog", { name: "Stop this research?" });
const row = (page: Page, stage: string) => page.locator(`#stage-user-stopped #spine > li[data-stage="${stage}"]`);
/* Researching has just become the active row: about 3 s of its first pass remain (see Task 9's note). */
const researching = (page: Page) => page.locator('#spine li[data-stage="researcher"][data-state="active"]').waitFor({ timeout: 15_000 });
async function stopNow(page: Page) {
  await stop(page).click();
  await popover(page).getByRole("button", { name: "Stop research" }).click();
  await expect(page.locator("#stage-user-stopped")).toBeVisible();
}

test.beforeEach(async ({ context }) => {
  // The longest case: its paced stream keeps each step on screen long enough to stop it there.
  await context.setExtraHTTPHeaders({ "X-Replay-Case": "missing-target-triggers-one-extra-pass" });
});

test("Stop is offered through the run, gone once Publishing starts, and a stop after the end is refused (AC30, AC33)", async ({ page, request }) => {
  const id = await submit(page, QUESTION);
  await researching(page);
  await expect(stop(page)).toHaveText("Stop");
  await page.locator('#spine li[data-stage="finalize_report"][data-state="active"]').waitFor({ timeout: 30_000 });
  await expect(stop(page)).toHaveCount(0);
  await waitTerminal(request, id);
  await expect(page.locator("#stage-report")).toBeVisible({ timeout: 20_000 });
  await expect(stop(page)).toHaveCount(0);
  const refused = await request.post(`${API}/research/${id}/stop`);
  expect(refused.status()).toBe(409);
  expect((await refused.json()).error).toEqual({ code: "not_stoppable", message: "Research session can no longer be stopped.", reason: "finished", issues: [] });
});

test("confirm and stop mid-run: the stopped stage, its chip, the frozen pipeline, the sidebar and the API's answers (AC28, AC31-AC33)", async ({ page, request }) => {
  const id = await submit(page, QUESTION);
  await researching(page);
  await stop(page).click();
  await expect(popover(page).locator(".b-sub")).toHaveText("It stops right away and nothing more is spent. What's done so far stays here, but no report is written.");
  await expect(popover(page).getByRole("button", { name: "Keep going" })).toBeFocused();
  await popover(page).getByRole("button", { name: "Stop research" }).click();
  await expect(page.locator("#stage-user-stopped")).toBeVisible();
  // Task 8: after a real stop focus moves to the stopped stage's first line (it was on the popover's button).
  await expect(page.locator("#stoppedLine")).toBeFocused();
  const status = await (await request.get(`${API}/research/${id}/status`)).json();
  expect([status.status, status.stopped_step]).toEqual(["stopped", "researcher"]);
  expect(status.finished_at).not.toBeNull();
  await expect(chip(page)).toHaveText("Stopped by you · at Researching");
  await expect(chip(page).locator(".dot")).toHaveClass("dot dot-neutral");
  await expect(page.locator(".stopped-note .b-now")).toHaveText(/^You stopped this research at \d\d:\d\d, (less than a minute|1 minute|\d+ minutes) in\.$/);
  await expect(page.locator(".stopped-note .b-sub")).toHaveText("No report was written. The plan and what research found so far are kept below until the service restarts.");
  await expect(row(page, "planner")).toHaveAttribute("data-state", "done");
  await expect(row(page, "researcher")).toHaveAttribute("data-state", "stopped");
  await expect(row(page, "researcher").locator(".m-live")).toHaveText(/^Stopped( · .+)?$/);
  for (const stage of ["source_evaluator", "evidence_verifier", "report_writer", "report_reviewer", "finalize_report"]) {
    await expect(row(page, stage)).toHaveAttribute("data-state", "off");
    await expect(row(page, stage).locator(".m-live")).toHaveText("not run");
  }
  await row(page, "planner").locator("button.ps-toggle").click();
  await expect(row(page, "planner")).toHaveAttribute("data-open", "1");
  await expect(page.locator("#noteLine")).toHaveCount(0);
  await expect(stop(page)).toHaveCount(0);
  const stream = await (await request.get(`${API}/research/${id}/stream`)).text();
  expect(stream.trimEnd().split(/\r?\n\r?\n/).at(-1)).toContain("event: session.stopped");
  for (const [path, code] of [["report", "report_unavailable"], ["evidence", "evidence_unavailable"], ["evidence?format=markdown", "evidence_unavailable"]] as const) {
    const answer = await request.get(`${API}/research/${id}/${path}`);
    expect([answer.status(), (await answer.json()).error.code]).toEqual([409, code]);
  }
  const note = await request.post(`${API}/research/${id}/notes`, { data: { text: "too late" } });
  expect([note.status(), (await note.json()).error.code]).toEqual([409, "notes_closed"]);
  // D29: the sidebar names the session as stopped in its accessible name only.
  const item = page.locator(`#sessionList [data-session="${id}"]`);
  await expect(item).toHaveAttribute("aria-label", /— stopped by you$/);
  await expect(item).toHaveAttribute("data-run", "0");
});

test("Keep going, Escape and a click outside close the confirmation and give focus back to Stop (AC33)", async ({ page, request }) => {
  const id = await submit(page, QUESTION);
  await researching(page);
  await stop(page).click();
  await popover(page).getByRole("button", { name: "Keep going" }).click();
  await expect(popover(page)).toHaveCount(0);
  await expect(stop(page)).toBeFocused();
  await stop(page).click();
  await page.keyboard.press("Escape");
  await expect(popover(page)).toHaveCount(0);
  await expect(stop(page)).toBeFocused();
  await stop(page).click();
  await page.locator("#running-h").click();
  await expect(popover(page)).toHaveCount(0);
  await expect(stop(page)).toBeFocused();
  expect((await (await request.get(`${API}/research/${id}/status`)).json()).status).toBe("running");
  await waitTerminal(request, id);
});

test("a refusal that reaches the open confirmation says it is too late (AC33)", async ({ page, request }) => {
  const id = await submit(page, QUESTION);
  await researching(page);
  // The page hides Stop as its stream shows Publishing, and the API refuses from that same event, so a
  // real 409 reaches an open popover only inside a few milliseconds (spec ambiguity 17): this is the
  // API's own refusal body, answered for the page's POST.
  await page.route("**/api/research/*/stop", (route) => route.fulfill({
    status: 409, contentType: "application/json",
    body: JSON.stringify({ error: { code: "not_stoppable", message: "Research session can no longer be stopped.", reason: "publishing", issues: [] } }),
  }));
  await stop(page).click();
  await popover(page).getByRole("button", { name: "Stop research" }).click();
  await expect(page.locator("#stopTooLate")).toHaveText("Too late to stop — the research is finishing.");
  await expect(popover(page).getByRole("button", { name: "Close" })).toBeFocused();
  await popover(page).getByRole("button", { name: "Close" }).click();
  await expect(popover(page)).toHaveCount(0);
  await waitTerminal(request, id);
  expect((await (await request.get(`${API}/research/${id}/status`)).json()).status).toBe("completed");
});

test("Ask again starts a new session with the same question (AC33)", async ({ page, request }) => {
  const id = await submit(page, QUESTION);
  await researching(page);
  await stopNow(page);
  const question = await page.locator("#user-stopped-h").textContent();
  await page.locator("#askAgain").click();
  await page.waitForURL((url) => /\/research\/[0-9a-f]+$/.test(url.pathname) && !url.pathname.endsWith(id));
  const fresh = page.url().split("/").pop()!;
  expect((await (await request.get(`${API}/research/${fresh}/status`)).json()).query).toBe(question);
  expect((await (await request.get(`${API}/research/${id}/status`)).json()).status).toBe("stopped");
  await waitTerminal(request, fresh);
});

test("a stop while the one-time check asks shows no pipeline card (D33)", async ({ page, request, context }) => {
  await context.setExtraHTTPHeaders({ "X-Replay-Case": "missing-target-triggers-one-extra-pass", "X-Replay-Clarify": "on" });
  const id = await submit(page, QUESTION);
  await expect(page.locator("#clarifyCard")).toBeVisible({ timeout: 10_000 });
  await stopNow(page);
  await expect(page.locator(".stopped-note .b-now")).toHaveText(/^You stopped this research at \d\d:\d\d, before it started\.$/);
  await expect(chip(page)).toHaveText("Stopped by you · at the questions");
  await expect(page.locator("#stage-user-stopped .card")).toHaveCount(0);
  expect((await (await request.get(`${API}/research/${id}/status`)).json()).stopped_step).toBe("check");
});

test.describe("on a phone", () => {
  test.use({ viewport: { width: 390, height: 844 } });
  test("Stop keeps its label, the confirmation fits the screen, and the stopped stage has no sideways scroll (AC33)", async ({ page }) => {
    await submit(page, QUESTION);
    await researching(page);
    await expect(stop(page)).toHaveText("Stop");
    await stop(page).click();
    const box = (await popover(page).boundingBox())!;
    expect(box.x).toBeGreaterThanOrEqual(0);
    expect(box.x + box.width).toBeLessThanOrEqual(390);
    await popover(page).getByRole("button", { name: "Stop research" }).click();
    await expect(page.locator("#stage-user-stopped")).toBeVisible();
    const [scroll, inner] = await page.evaluate(() => [document.scrollingElement!.scrollWidth, window.innerWidth]);
    expect(scroll).toBeLessThanOrEqual(inner);
  });
});
