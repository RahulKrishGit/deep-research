import { expect, test } from "@playwright/test";
import { submit, waitTerminal } from "./support";

const ACME = "What was the Acme widget adoption rate in the United States in 2024?";

test("submit → Submitted beat → Running; the sidebar shows the case's question", async ({ page, request }) => {
  const id = await submit(page, "How mature is quantum error correction?");
  await expect(page.locator("#stage-submitted")).toBeVisible();
  await expect(page.locator("#submitted-h")).toHaveText(ACME);
  await expect(page.locator("#stage-running")).toBeVisible({ timeout: 5_000 });
  // The planner finishes inside the 2.2 s beat: the first Running render already has Planning done.
  await expect(page.locator('li[data-stage="planner"]')).toHaveAttribute("data-state", "done");
  const active = await page.locator("li[data-stage][data-state='active']").getAttribute("data-stage");
  expect(["researcher", "source_evaluator", "evidence_verifier", "report_writer", "report_reviewer", "finalize_report"]).toContain(active);
  await expect(page.locator("#topbarStatus .chip")).toContainText("Running · pass 1 of 2");
  await expect(page.locator(`.sb-item[data-session="${id}"] .q`)).toHaveText(ACME);
  await expect(page.locator("#runElapsed")).toContainText("elapsed");
  await waitTerminal(request, id);
  await expect(page.locator("#topbarStatus .chip")).not.toContainText("Running", { timeout: 20_000 });
});

test("a reload mid-run rebuilds the running stage from the replay", async ({ page, request }) => {
  const id = await submit(page, "q");
  await expect(page.locator("#runNow")).toHaveText("Researching", { timeout: 10_000 });
  const before = await page.locator("#runElapsed").textContent();
  await page.reload();
  await expect(page.locator("#stage-running")).toBeVisible();
  await expect(page.locator("#stage-submitted")).toHaveCount(0);
  await expect(page.locator('li[data-stage="planner"]')).toHaveAttribute("data-state", "done");
  const after = await page.locator("#runElapsed").textContent();
  expect(after! >= before!).toBe(true); // "MM:SS elapsed" compares lexically
  await waitTerminal(request, id);
});
