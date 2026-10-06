import { expect, test } from "@playwright/test";
import { API, submit, waitTerminal } from "./support";

test("the default case ends on the Report stage with the body, the rail and both downloads", async ({ page, request }) => {
  const id = await submit(page, "q");
  await waitTerminal(request, id);
  await expect(page.locator("#stage-report")).toBeVisible({ timeout: 20_000 });
  await expect(page.locator("#topbarStatus .chip")).toContainText(/^Completed · review accepted · \d\.\d\d/);
  // The evidence line is lifted out of the cards; each card keeps its h2.
  await expect(page.locator("#reportEvidence")).toContainText(/^Evidence as of|^No source could be checked/);
  await expect(page.locator("#stage-report .rsec .prose h2", { hasText: "Bottom line" })).toBeVisible();
  await expect(page.locator("#stage-report .rsec .prose h2", { hasText: "Sources" })).toBeVisible();
  await expect(page.locator("#stage-report")).not.toContainText("Executive Summary");
  for (const sel of ["#downloadBtn", "#downloadEvidenceBtn"]) {
    const href = await page.locator(sel).getAttribute("href");
    expect((await request.get(`http://127.0.0.1:3010${href}`)).status()).toBe(200);
  }
  await expect(page.locator("#repFactPass")).toHaveText("Went back once to fill gaps");
});

test("review unavailable: Partially completed · review unavailable, not scored", async ({ page, request, context }) => {
  await context.setExtraHTTPHeaders({ "X-Replay-Case": "review-unavailable" });
  const id = await submit(page, "q");
  await waitTerminal(request, id);
  await expect(page.locator("#topbarStatus .chip")).toHaveText(/Partially completed · review unavailable$/, { timeout: 20_000 });
  await expect(page.locator("#repReviewStatus")).toHaveText("review unavailable");
  await expect(page.locator("#repReviewN")).toHaveText("not scored");
});

test("empty but clean: extra passes used · 3 targets not found, with the engine's ids and questions", async ({ page, request, context }) => {
  await context.setExtraHTTPHeaders({ "X-Replay-Case": "empty-but-clean" });
  const id = await submit(page, "q");
  await waitTerminal(request, id);
  await expect(page.locator("#topbarStatus .chip")).toHaveText("Partially completed · extra passes used · 3 targets not found", { timeout: 20_000 });
  await expect(page.locator("#stage-report")).toBeVisible();
  await expect(page.locator("#repCovNotFound")).toContainText("topic-01-target-01 — ");
  // Three not-found targets in this case — assert all three, not just the first and last.
  await expect(page.locator("#repCovNotFound")).toContainText("topic-02-target-01 — ");
  await expect(page.locator("#repCovNotFound")).toContainText("topic-03-target-01 — ");
  await expect(page.locator("#stage-report")).not.toContainText("Executive Summary");
});

test("the Evidence view lists the run's findings, filters, and opens from the report's link", async ({ page, request, context }) => {
  await context.setExtraHTTPHeaders({ "X-Replay-Case": "extra-pass-finds-nothing" });
  const id = await submit(page, "q");
  await waitTerminal(request, id);
  await expect(page.locator("#stage-report")).toBeVisible({ timeout: 20_000 });
  await page.locator("#segView button[data-view='evidence']").click();
  await expect(page.locator("#stage-report")).toHaveAttribute("data-view", "evidence");
  await expect(page.locator(".chip-filter[data-filter='all'] .n")).not.toHaveText("0");
  await expect(page.locator(".ev-row[aria-selected='true']")).toHaveCount(1);
  await expect(page.locator("#evDetail a.tlink").first()).toBeVisible();
  await page.locator(".chip-filter[data-filter='dropped']").click();
  await expect(page.locator(".ev-row")).toHaveCount(1);
  await expect(page.locator(".ev-row .lbl")).toHaveText("X01");
  await page.locator("#segView button[data-view='report']").click();
  await page.locator("#repEvidenceLink button.link").click();
  await expect(page.locator("#stage-report")).toHaveAttribute("data-view", "evidence");
  const e1 = await (await request.get(`${API}/research/${id}/evidence`)).json();
  expect(e1.findings.filter((f: { status: string | null }) => f.status === "dropped")).toHaveLength(1);
});
