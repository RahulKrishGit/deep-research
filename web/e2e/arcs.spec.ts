import { expect, test } from "@playwright/test";
import { installTransitionRecorder, submit, transitions, waitTerminal } from "./support";

test("the extra pass lights the amber arc: flowing then settled", async ({ page, request, context }) => {
  await context.setExtraHTTPHeaders({ "X-Replay-Case": "missing-target-triggers-one-extra-pass" });
  await installTransitionRecorder(page);
  const id = await submit(page, "q");
  await expect(page.locator('#spineWrap[data-arc="extra_pass"]')).toHaveAttribute("data-loop", "settled", { timeout: 30_000 });
  await expect(page.locator('li[data-stage="researcher"] .stage-meta').first()).toHaveText("1 missing target only");
  await waitTerminal(request, id);
  const seen = (await transitions(page)).filter((t) => t.loop !== null).map((t) => `${t.loop}/${t.arc}`);
  expect(seen).toContain("flowing/extra_pass");
  expect(seen.indexOf("flowing/extra_pass")).toBeLessThan(seen.indexOf("settled/extra_pass"));
});

test("the redraft lights the grey arc", async ({ page, request, context }) => {
  await context.setExtraHTTPHeaders({ "X-Replay-Case": "scoped-redraft-after-a-named-defect" });
  await installTransitionRecorder(page);
  const id = await submit(page, "q");
  await expect(page.locator('#spineWrap[data-arc="redraft"]')).toHaveAttribute("data-loop", "settled", { timeout: 30_000 });
  await waitTerminal(request, id);
  const seen = (await transitions(page)).filter((t) => t.loop !== null).map((t) => `${t.loop}/${t.arc}`);
  expect(seen).toContain("flowing/redraft");
  expect(seen.indexOf("flowing/redraft")).toBeLessThan(seen.indexOf("settled/redraft"));
});
