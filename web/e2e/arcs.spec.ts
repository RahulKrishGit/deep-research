import { expect, test } from "@playwright/test";
import { installTransitionRecorder, submit, transitions, waitTerminal } from "./support";

test("the extra pass lights the amber arc: flowing then settled, and Researching says why it reopened", async ({ page, request, context }) => {
  await context.setExtraHTTPHeaders({ "X-Replay-Case": "missing-target-triggers-one-extra-pass" });
  await installTransitionRecorder(page);
  const id = await submit(page, "q");
  await expect(page.locator('#spineWrap[data-arc="extra_pass"]')).toHaveAttribute("data-loop", "settled", { timeout: 30_000 });
  const why = page.locator('#spine li[data-stage="researcher"] .b-why');
  await expect(why).toHaveText("Going back to research 1 gap the review found");
  await expect(why).toHaveAttribute("data-kind", "extra_pass");
  await waitTerminal(request, id);
  const seen = (await transitions(page)).filter((t) => t.loop !== null).map((t) => `${t.loop}/${t.arc}`);
  expect(seen).toContain("flowing/extra_pass");
  expect(seen.indexOf("flowing/extra_pass")).toBeLessThan(seen.indexOf("settled/extra_pass"));
});

test("the redraft lights the grey arc and Writing says why it reopened", async ({ page, request, context }) => {
  await context.setExtraHTTPHeaders({ "X-Replay-Case": "scoped-redraft-after-a-named-defect" });
  await installTransitionRecorder(page);
  const id = await submit(page, "q");
  await expect(page.locator('#spineWrap[data-arc="redraft"]')).toHaveAttribute("data-loop", "settled", { timeout: 30_000 });
  const why = page.locator('#spine li[data-stage="report_writer"] .b-why');
  await expect(why).toHaveText("Rewriting to fix 1 issue the review found");
  await expect(why).toHaveAttribute("data-kind", "redraft");
  await waitTerminal(request, id);
  const seen = (await transitions(page)).filter((t) => t.loop !== null).map((t) => `${t.loop}/${t.arc}`);
  expect(seen).toContain("flowing/redraft");
  expect(seen.indexOf("flowing/redraft")).toBeLessThan(seen.indexOf("settled/redraft"));
});
