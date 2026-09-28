import { expect, test } from "@playwright/test";
import { API, submit, waitTerminal } from "./support";

test("the sidebar lists sessions newest first, refreshes while one runs, and states its memory", async ({ page, request }) => {
  const first = await submit(page, "first");
  const second = await submit(page, "second");
  const ids = await page.locator(".sb-item").evaluateAll((els) => els.map((e) => e.getAttribute("data-session")));
  expect(ids.indexOf(second)).toBeLessThan(ids.indexOf(first));
  // a session started elsewhere appears without a reload while one runs
  const posted = await request.post(`${API}/research`, { data: { query: "elsewhere" } });
  const { session_id: third } = await posted.json();
  await expect(page.locator(`.sb-item[data-session="${third}"]`)).toBeVisible({ timeout: 8_000 });
  await expect(page.locator(".sb-foot")).toHaveText("Sessions are held in the service process's memory; this list empties when the service restarts.");
  for (const id of [first, second, third]) await waitTerminal(request, id);
});
