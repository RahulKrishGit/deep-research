import { expect, test } from "@playwright/test";
import { submit, waitTerminal } from "./support";

test("an API-level failure lands on the Failed stage with the plain-words headline and its reason", async ({ page, request, context }) => {
  await context.setExtraHTTPHeaders({ "X-Replay-Case": "no-such-case" });
  const id = await submit(page, "typed question");
  await waitTerminal(request, id);
  await expect(page.locator("#stage-failed")).toBeVisible({ timeout: 20_000 });
  await expect(page.locator("#topbarStatus .chip")).toHaveText("Failed · halted");
  await expect(page.locator("#failedType")).toHaveText("Service configuration error");
  await expect(page.locator("#failFactType")).toHaveText("api.research.configuration_error");
  await expect(page.locator("#failFactReason")).toHaveText("config_invalid");
  await expect(page.locator("#failed-h")).toHaveText("typed question");
  await expect(page.locator('#spineFailed li[data-state="done"]')).toHaveCount(0);
  await expect(page.locator('#spineFailed li[data-stage="finalize_report"]')).toHaveAttribute("data-state", "skipped");
  await expect(page.locator("#failedCounters dd").first()).toHaveText("not reached");
  await expect(page.locator("#downloadBtn")).toHaveCount(0);
  await expect(page.locator("#stage-failed button[disabled]")).toHaveCount(0);
});
