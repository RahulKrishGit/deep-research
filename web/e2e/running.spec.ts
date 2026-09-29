import { expect, test } from "@playwright/test";
import { firstRunningRender, installTransitionRecorder, submit, waitTerminal } from "./support";

const ACME = "What was the Acme widget adoption rate in the United States in 2024?";

test("submit → Submitted beat → Running; the sidebar shows the case's question", async ({ page, request }) => {
  await installTransitionRecorder(page);
  const id = await submit(page, "How mature is quantum error correction?");
  await expect(page.locator("#stage-submitted")).toBeVisible();
  await expect(page.locator("#submitted-h")).toHaveText(ACME);
  await expect(page.locator("#stage-running")).toBeVisible({ timeout: 5_000 });
  // The planner finishes inside the 2.2 s beat: the first Running render already has Planning done —
  // proved from a childList snapshot taken the instant #stage-running was inserted, not a polling
  // toHaveAttribute alone, which would also pass if it mounted active and changed afterwards.
  await expect(page.locator('li[data-stage="planner"]')).toHaveAttribute("data-state", "done");
  const firstRender = await firstRunningRender(page);
  expect(firstRender?.find((s) => s.stage === "planner")?.state).toBe("done");
  const active = await page.locator("li[data-stage][data-state='active']").getAttribute("data-stage");
  expect(["researcher", "source_evaluator", "evidence_verifier", "report_writer", "report_reviewer", "finalize_report"]).toContain(active);
  // live-briefs spec §4.2 (AC3): the chip names the step; the running stage has no Now header,
  // no counters block and no pass text.
  await expect(page.locator("#topbarStatus .chip")).toHaveText(/^Running · (Planning|Researching|Evaluating sources|Verifying evidence|Writing report|Reviewing|Publishing)$/);
  for (const gone of [".pipe-now", "#runNow", "#runPasses", "#runLoopTag", "#runCounters"]) await expect(page.locator(`#stage-running ${gone}`)).toHaveCount(0);
  expect(await page.locator("#stage-running").textContent()).not.toMatch(/\bpass\b/i);
  await expect(page.locator(`.sb-item[data-session="${id}"] .q`)).toHaveText(ACME);
  await expect(page.locator("#runElapsed")).toContainText("elapsed");
  await waitTerminal(request, id);
  await expect(page.locator("#topbarStatus .chip")).not.toContainText("Running", { timeout: 20_000 });
});

test("a reload mid-run rebuilds the running stage from the replay", async ({ page, request }) => {
  const id = await submit(page, "q");
  await expect(page.locator('#spine li[data-stage="researcher"][data-state="active"]')).toBeVisible({ timeout: 10_000 });
  const before = await page.locator("#runElapsed").textContent();
  await page.reload();
  await expect(page.locator("#stage-running")).toBeVisible();
  await expect(page.locator("#stage-submitted")).toHaveCount(0);
  await expect(page.locator('li[data-stage="planner"]')).toHaveAttribute("data-state", "done");
  const active = await page.locator("li[data-stage][data-state='active']").getAttribute("data-stage");
  expect(["researcher", "source_evaluator", "evidence_verifier", "report_writer", "report_reviewer", "finalize_report"]).toContain(active);
  const after = await page.locator("#runElapsed").textContent();
  expect(after! >= before!).toBe(true); // "MM:SS elapsed" compares lexically
  await waitTerminal(request, id);
});
