import { expect, type APIRequestContext, type Page } from "@playwright/test";

export const API = "http://127.0.0.1:8010";
export const DEAD_APP = "http://127.0.0.1:3011";
export const deadPort = () => process.env.DEEP_RESEARCH_DEAD_PORT!;

export interface Transition { loop: string | null; arc: string | null; state: string | null; stage: string | null }
export interface StageSnapshot { stage: string | null; state: string | null }
/* Record every data-loop/data-arc/data-state change before the page's scripts run: the flowing arc
   state lasts ≈ 450 ms at the default pacing, shorter than a polling assertion's back-off.
   A later attribute mutation can only prove a row *changed after* it existed — the very first
   value an element is created with is never a mutation record. So a childList observer also
   snapshots every li[data-stage]'s data-state the instant #stage-running itself is inserted,
   which is the only way to prove what "the first Running render" actually painted (T-E1). */
export async function installTransitionRecorder(page: Page): Promise<void> {
  await page.addInitScript(() => {
    const w = window as unknown as { __drTransitions: Transition[]; __drFirstRunningRender: StageSnapshot[] | null };
    w.__drTransitions = [];
    w.__drFirstRunningRender = null;
    const record = (t: Element) => w.__drTransitions.push({ loop: t.getAttribute("data-loop"), arc: t.getAttribute("data-arc"), state: t.getAttribute("data-state"), stage: t.getAttribute("data-stage") });
    const snapshotFirstRunningRender = (node: Element) => {
      if (w.__drFirstRunningRender) return;
      const stageRunning = node.id === "stage-running" ? node : node.querySelector("#stage-running");
      if (!stageRunning) return;
      w.__drFirstRunningRender = [...stageRunning.querySelectorAll("li[data-stage]")].map((li) => ({ stage: li.getAttribute("data-stage"), state: li.getAttribute("data-state") }));
    };
    new MutationObserver((mutations) => {
      for (const m of mutations) {
        if (m.type === "attributes" && m.target instanceof Element && (m.target.id === "spineWrap" || m.target.matches("li[data-stage]"))) record(m.target);
        if (m.type === "childList") for (const node of m.addedNodes) if (node instanceof Element) snapshotFirstRunningRender(node);
      }
    }).observe(document, { attributes: true, childList: true, subtree: true, attributeFilter: ["data-loop", "data-arc", "data-state"] });
  });
}
export const transitions = (page: Page) => page.evaluate(() => (window as unknown as { __drTransitions: Transition[] }).__drTransitions);
/* The li[data-stage] states exactly as they were the instant #stage-running was first inserted —
   null until that has happened. */
export const firstRunningRender = (page: Page) => page.evaluate(() => (window as unknown as { __drFirstRunningRender: StageSnapshot[] | null }).__drFirstRunningRender);

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
