import { expect, type APIRequestContext, type Page } from "@playwright/test";

export const API = "http://127.0.0.1:8010";
export const DEAD_APP = "http://127.0.0.1:3011";
export const deadPort = () => process.env.DEEP_RESEARCH_DEAD_PORT!;

export interface Transition { loop: string | null; arc: string | null; state: string | null; stage: string | null }
/* Record every data-loop/data-arc/data-state change before the page's scripts run: the flowing arc
   state lasts ≈ 450 ms at the default pacing, shorter than a polling assertion's back-off. */
export async function installTransitionRecorder(page: Page): Promise<void> {
  await page.addInitScript(() => {
    const w = window as unknown as { __drTransitions: Transition[] };
    w.__drTransitions = [];
    const record = (t: Element) => w.__drTransitions.push({ loop: t.getAttribute("data-loop"), arc: t.getAttribute("data-arc"), state: t.getAttribute("data-state"), stage: t.getAttribute("data-stage") });
    new MutationObserver((mutations) => {
      for (const m of mutations) if (m.target instanceof Element && (m.target.id === "spineWrap" || m.target.matches("li[data-stage]"))) record(m.target);
    }).observe(document, { attributes: true, subtree: true, attributeFilter: ["data-loop", "data-arc", "data-state"] });
  });
}
export const transitions = (page: Page) => page.evaluate(() => (window as unknown as { __drTransitions: Transition[] }).__drTransitions);

export async function submit(page: Page, question: string): Promise<string> {
  await page.goto("/");
  await page.getByLabel("Research question").fill(question);
  await page.getByRole("button", { name: "Start research" }).click();
  await page.waitForURL(/\/research\/[0-9a-f]+$/);
  return page.url().split("/").pop()!;
}
export async function waitTerminal(request: APIRequestContext, sessionId: string): Promise<void> {
  await expect.poll(async () => (await (await request.get(`${API}/research/${sessionId}/status`)).json()).status, { timeout: 60_000 }).not.toBe("running");
}
