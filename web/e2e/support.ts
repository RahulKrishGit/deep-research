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
/* Terminal: neither running nor waiting for the reader's answers (needs_input, live-briefs spec §4.4);
   a session the reader stopped is terminal too (notes-progress-report spec §8.4). */
export async function waitTerminal(request: APIRequestContext, sessionId: string): Promise<void> {
  await expect.poll(async () => (await (await request.get(`${API}/research/${sessionId}/status`)).json()).status, { timeout: 60_000 }).toMatch(/^(completed|max_iterations|incomplete|failed|stopped)$/);
}

/* live-briefs spec AC6: for each pair of adjacent rows, how far the upper row's connector (its
   ::before, measured from its padding box) starts from its own node's centre and ends from the next
   node's centre, in px. */
export const connectorOffsets = (page: Page) => page.evaluate(() => {
  const rows = [...document.querySelectorAll<HTMLElement>("#spine > li[data-stage]")];
  return rows.slice(0, -1).map((li, i) => {
    const r = li.getBoundingClientRect();
    const cs = getComputedStyle(li), line = getComputedStyle(li, "::before");
    const top = r.top + parseFloat(cs.borderTopWidth) + parseFloat(line.top);
    const bottom = r.bottom - parseFloat(cs.borderBottomWidth) - parseFloat(line.bottom);
    const a = li.querySelector(".bullet")!.getBoundingClientRect(), b = rows[i + 1].querySelector(".bullet")!.getBoundingClientRect();
    return { pair: li.dataset.stage + ">" + rows[i + 1].dataset.stage, top: top - (a.top + a.height / 2), bottom: bottom - (b.top + b.height / 2) };
  });
});

/* live-briefs spec AC7: every CSS transition the running spine starts, with the delay and duration it
   was started with — read from the element's computed transition lists at transitionrun, the moment
   a transition is created (its delay phase included). `part` names what moved; `handoff`/`open` are
   the row's roles at that moment. */
export interface MotionRecord { stage: string | null; handoff: string | null; open: string | null; part: string; prop: string; delay: number; duration: number }
export async function installMotionRecorder(page: Page): Promise<void> {
  await page.addInitScript(() => {
    const w = window as unknown as { __drMotion: MotionRecord[] };
    w.__drMotion = [];
    const ms = (v: string) => (v.trim().endsWith("ms") ? parseFloat(v) : parseFloat(v) * 1000);
    const partOf = (el: Element, pseudo: string): string => {
      if (pseudo) return "connector";
      if (el.classList.contains("ps-x")) return "ps-x";
      if (el.classList.contains("ack")) return "ack";
      if (el.classList.contains("ln")) return "line";
      if (el.classList.contains("bullet")) return "bullet";
      if (el.classList.contains("m-live")) return "m-live";
      if (el.classList.contains("m-out")) return "m-out";
      // notes-progress-report spec §6.3-§6.7: one text of a cross-fading stack (a status line, a fact).
      if (el.parentElement?.classList.contains("xf")) return "xf";
      if (el.classList.contains("dotc")) return "dot";
      if (el.tagName.toLowerCase() === "path" && el.closest(".mk")) return "check";
      if (el.matches("li[data-stage]")) return "row";
      return el.tagName.toLowerCase();
    };
    document.addEventListener("transitionrun", (event) => {
      const e = event as TransitionEvent;
      const el = e.target as Element;
      const row = el.closest("#spine > li[data-stage]");
      if (!row) return;
      const style = getComputedStyle(el, e.pseudoElement || null);
      const props = style.transitionProperty.split(",").map((p) => p.trim());
      const delays = style.transitionDelay.split(","), durations = style.transitionDuration.split(",");
      // A shorthand in the list starts its transitions on longhands: background → background-color,
      // border-color → border-top-color and its three siblings. A property the list does not name at
      // all records -1/-1, so no assertion can match it by borrowing another property's timing.
      const shorthand = e.propertyName === "background-color" ? "background"
        : /^border-(top|right|bottom|left)-color$/.test(e.propertyName) ? "border-color" : e.propertyName;
      let i = props.indexOf(e.propertyName);
      if (i < 0) i = props.indexOf(shorthand);
      if (i < 0) i = props.indexOf("all");
      w.__drMotion.push({ stage: row.getAttribute("data-stage"), handoff: row.getAttribute("data-handoff"), open: row.getAttribute("data-open"),
        part: partOf(el, e.pseudoElement), prop: e.propertyName,
        delay: i < 0 ? -1 : ms(delays[i % delays.length]), duration: i < 0 ? -1 : ms(durations[i % durations.length]) });
    }, true);
  });
}
export const motion = (page: Page) => page.evaluate(() => (window as unknown as { __drMotion: MotionRecord[] }).__drMotion);

/* live-briefs spec §4.5: whether #stage-clarify was ever on the page, and every summary line the
   check showed, recorded from before the page's scripts run — the summary can be on screen for a
   fraction of a second before the planner starts and the running stage takes over. */
export interface ClarifyRecord { stage: boolean; summaries: string[] }
export async function installClarifyRecorder(page: Page): Promise<void> {
  await page.addInitScript(() => {
    const w = window as unknown as { __drClarify: ClarifyRecord };
    w.__drClarify = { stage: false, summaries: [] };
    new MutationObserver(() => {
      if (document.getElementById("stage-clarify")) w.__drClarify.stage = true;
      const line = document.getElementById("clarifySummary")?.textContent ?? "";
      if (line && w.__drClarify.summaries.at(-1) !== line) w.__drClarify.summaries.push(line);
    }).observe(document, { childList: true, subtree: true, characterData: true });
  });
}
export const clarifyRecord = (page: Page) => page.evaluate(() => (window as unknown as { __drClarify: ClarifyRecord }).__drClarify);

/* live-briefs spec §4.7 (AC15, AC19): when each note POST was sent, when each acknowledgement was
   first fully shown, and every caption the note line showed — recorded from before the page's
   scripts run, because the "notes are closed" caption can be on screen for well under a second
   before the report stage replaces the running one. Times are performance.now() in the page.
   AC15 is timed from the send: `session.note.interpreted` is published only after the server has
   the note, so it cannot reach the page before the POST left it, and a bound measured from the send
   holds for the event too (the POST's 202 would not do: it and the event's frame reach the page
   over separate connections, and nothing orders them). */
export interface NoteRecord { sent: number[]; acks: { text: string; at: number }[]; closed: string[] }
export async function installNoteRecorder(page: Page): Promise<void> {
  await page.addInitScript(() => {
    const w = window as unknown as { __drNotes: NoteRecord };
    w.__drNotes = { sent: [], acks: [], closed: [] };
    const original = window.fetch.bind(window);
    window.fetch = async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
      if (init?.method === "POST" && /\/notes$/.test(url)) w.__drNotes.sent.push(performance.now());
      return original(input, init);
    };
    const tick = () => {
      for (const el of document.querySelectorAll<HTMLElement>(".ack")) {
        const text = el.textContent ?? "";
        if (text.startsWith("Got it") && Number(getComputedStyle(el).opacity) >= 0.99 && !w.__drNotes.acks.some((a) => a.text === text)) w.__drNotes.acks.push({ text, at: performance.now() });
      }
      const closed = document.getElementById("noteClosed")?.textContent ?? "";
      if (closed && !w.__drNotes.closed.includes(closed)) w.__drNotes.closed.push(closed);
      requestAnimationFrame(tick);
    };
    requestAnimationFrame(tick);
  });
}
export const noteRecord = (page: Page) => page.evaluate(() => (window as unknown as { __drNotes: NoteRecord }).__drNotes);
