import { expect, test } from "@playwright/test";

test("an unknown session says it is not in the service's memory", async ({ page }) => {
  await page.goto("/research/does-not-exist");
  await expect(page.getByText("This session isn't in the service's memory — sessions are lost when the API restarts.")).toBeVisible();
  // exact: true — the sidebar's persistent "New Research" button (Sidebar.tsx) matches the
  // default case-insensitive substring name search too; only the case differs from this one.
  await page.getByRole("button", { name: "New research", exact: true }).click();
  await expect(page).toHaveURL("http://127.0.0.1:3010/");
  await expect(page.getByLabel("Research question")).toBeVisible();
});
