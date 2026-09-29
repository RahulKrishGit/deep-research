// The two DESIGN.md handoffs (docs/design/DESIGN.md:1359-1490), ported from the prototype's
// clearFlight (docs/design/prototype/index.html:1941), flyQuestionToLock (:2009), holdBeat
// (:2069-2118) and REPORT_HANDOFF/enterReport (:3195-3231).
//
// The prototype keeps every stage as a sibling `.stage` in one DOM, so `holdBeat` can read the
// destination's rect synchronously, before the browser ever paints the intermediate state. This
// app splits idle and running across two routes: Next.js unmounts the composer's page and mounts
// `/research/[id]` in its place, so the locked question's rect does not exist until the
// destination has rendered. The idle→running half of this module is the seam that crosses that
// route change — the origin (Composer) records the departure frame and starts draining page 1;
// the destination (SubmittedStage) reads it back, measures its own rect once laid out, and runs
// the lift. See .superpowers/ui-transitions-report.md for the full accounting of what could and
// could not carry over unchanged.
//
// The running→report half stays within one component tree (SessionScreen swaps RunningPipeline
// for ReportStage, never a route change), so it only needs the same one-shot "note, then consume"
// shape, not a route-crossing rect.

import { Q_CENTER_MAX } from "./format";

export function reducedMotion(): boolean {
  return typeof window !== "undefined" && typeof window.matchMedia === "function" && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}

/* Durations live in the stylesheet so there is one source of truth for them; the fallback here is
   only what runs when no stylesheet is loaded (e.g. a unit test).

   Next's production build minifies globals.css and its minifier rewrites a `900ms` literal to
   the shorter `.9s` — CSSOM's own canonical serialization for a <time> below 1s, and exactly what
   getComputedStyle hands back here in a built app (never in the unminified prototype, which is
   why this parses the unit instead of assuming milliseconds the way index.html:1616-1619 does). */
export function motionMs(name: string, fallback: number): number {
  if (typeof document === "undefined") return fallback;
  const raw = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  const match = /^(-?[\d.]+)(ms|s)$/.exec(raw);
  if (!match) return fallback;
  const n = parseFloat(match[1]);
  if (Number.isNaN(n)) return fallback;
  return match[2] === "s" ? n * 1000 : n;
}

export interface FlightRect { left: number; top: number; width: number; height: number }
export function rectOf(el: Element): FlightRect {
  const r = el.getBoundingClientRect();
  return { left: r.left, top: r.top, width: r.width, height: r.height };
}

/* PAGE2_HOLD_MS, ported (index.html:1611): the read-back pause after the box lands (or after the
   dissolve, with no travel) — long enough to read what was sent before the pipeline replaces it. */
export const PAGE2_HOLD_MS = 750;
/* holdBeat's return value (index.html:2069-2117, DESIGN.md:1330-1337 "the submitted beat"): the
   fixed budget the Submitted stage holds for, with or without the lift. Computed at submit time
   (session-store.ts's Submission.beatMs), since it depends on prefers-reduced-motion. */
export function submittedBeatBudgetMs(): number {
  const clearMs = motionMs("--motion-clear", 320);
  const dissolveMs = motionMs("--motion-dissolve", 200);
  if (reducedMotion()) return clearMs + dissolveMs + PAGE2_HOLD_MS;
  return clearMs + motionMs("--motion-lift", 900) + dissolveMs + PAGE2_HOLD_MS;
}

// ── idle → running ──────────────────────────────────────────────────────────
export interface PendingFlight { sessionId: string; question: string; from: FlightRect; box: HTMLElement | null }

let pendingFlight: PendingFlight | null = null;
// setTimeout resolves to NodeJS.Timeout in this project (@types/node is present and its globals
// take the browser's setTimeout overload), not the DOM lib's `number` — named here rather than
// read off ReturnType<typeof setTimeout>, since every handle in this module is one of these.
let flightTimers: NodeJS.Timeout[] = [];

/* clearFlight, ported (index.html:1941-1950): cancel every pending timer and remove the box, so
   nothing is ever left mid-flight or dropped onto a page it was never aimed at. */
export function clearIdleToRunningFlight(): void {
  flightTimers.forEach((t) => clearTimeout(t));
  flightTimers = [];
  if (pendingFlight?.box?.parentNode) pendingFlight.box.parentNode.removeChild(pendingFlight.box);
  pendingFlight = null;
}

/* Composer, beat one (index.html:2079-2086, DESIGN.md:1371-1377): start draining page 1 and
   return the wall-clock deadline the caller must wait out before beat two may start. */
export function beginIdleToRunningClear(stageId: string): number {
  clearIdleToRunningFlight();
  if (typeof document !== "undefined") document.getElementById(stageId)?.classList.add("is-clearing");
  return Date.now() + motionMs("--motion-clear", 320);
}

/* Rolls beat one back: a failed POST must restore the composer intact with its error shown
   (DESIGN.md issue 2's acceptance criterion), not leave page 1 faded out. */
export function cancelIdleToRunningClear(stageId: string): void {
  if (typeof document !== "undefined") document.getElementById(stageId)?.classList.remove("is-clearing");
  clearIdleToRunningFlight();
}

/* The hinge between beat one and beat two (index.html:2100-2116, DESIGN.md:1407-1422 "the two are
   indistinguishable"): once the POST has succeeded and the clear deadline has passed, the box is
   created at the composer's own frame — not the textarea, see runIdleToRunningLift — and the
   composer is hidden in the same synchronous step, then handed to the destination route via the
   module state below, since the composer itself is about to unmount. */
export async function armIdleToRunningFlight(opts: { sessionId: string; question: string; composerEl: HTMLElement | null; clearDeadline: number }): Promise<void> {
  const wait = opts.clearDeadline - Date.now();
  if (wait > 0) await new Promise<void>((resolve) => { flightTimers.push(setTimeout(resolve, wait)); });
  if (!opts.composerEl) { pendingFlight = { sessionId: opts.sessionId, question: opts.question, from: { left: 0, top: 0, width: 0, height: 0 }, box: null }; return; }
  const from = rectOf(opts.composerEl);
  if (!from.width || !from.height) { pendingFlight = { sessionId: opts.sessionId, question: opts.question, from, box: null }; return; }
  const box = document.createElement("div");
  box.className = "q-flight";
  box.setAttribute("aria-hidden", "true");
  const label = document.createElement("span");
  label.className = "q-flight-t";
  label.textContent = opts.question;
  box.appendChild(label);
  box.style.left = `${from.left}px`;
  box.style.top = `${from.top}px`;
  box.style.width = `${from.width}px`;
  box.style.height = `${from.height}px`;
  document.body.appendChild(box);
  opts.composerEl.classList.add("is-handing-off");
  pendingFlight = { sessionId: opts.sessionId, question: opts.question, from, box };
}

/* SubmittedStage: consume the pending flight for this session. A flight armed for a different
   session — the operator navigated elsewhere before it landed — is cancelled outright rather than
   left to land on whatever the new page happens to have at its target rect (the prototype's
   clearFlight rule, ported across a route boundary the single-page prototype never had). */
export function takeIdleToRunningFlight(sessionId: string): PendingFlight | null {
  if (!pendingFlight) return null;
  if (pendingFlight.sessionId !== sessionId) { clearIdleToRunningFlight(); return null; }
  const flight = pendingFlight;
  pendingFlight = null;
  return flight;
}

/* Beats two and three, ported from flyQuestionToLock (index.html:2009-2064) and the tail of
   holdBeat (:2100-2117). `toEl` is #submitted-h, measured by the caller while it is laid out but
   still held at opacity 0 (is-preparing). `onLanded` fires once the box starts to dissolve — the
   caller reveals its own content there, matching landed(true)/is-revealing. Falls back to calling
   onLanded on the next tick when there is no box (reduced motion, jsdom, or a collapsed frame) or
   no usable destination rect — the "no travel" branch (index.html:2100-2105): the record simply
   appears, rather than being carried to, but beats one and three still run. */
export function runIdleToRunningLift(flight: PendingFlight, toEl: Element, onLanded: () => void): void {
  const to = rectOf(toEl);
  const box = flight.box;
  if (!box || !flight.from.width || !flight.from.height || !to.width || !to.height) {
    flightTimers.push(setTimeout(onLanded, 0));
    return;
  }
  const liftMs = motionMs("--motion-lift", 900);
  const dissolveMs = motionMs("--motion-dissolve", 200);
  // commit the starting geometry before the coordinates change, or the transition has nothing to
  // run from (index.html:2027-2029) — load-bearing only when the box was just created on this
  // same page; across the route change the browser has already painted the `from` state.
  void box.offsetWidth;
  box.classList.add("is-locked");
  box.style.left = `${to.left}px`;
  box.style.top = `${to.top}px`;
  box.style.height = `${to.height}px`;
  const label = box.querySelector<HTMLElement>(".q-flight-t");
  if (label && flight.question.length <= Q_CENTER_MAX) {
    label.style.display = "block";
    const frameW = label.offsetWidth;
    label.style.display = "";
    const textW = label.offsetWidth;
    if (frameW > textW) label.style.transform = `translateX(${(frameW - textW) / 2}px)`;
  }
  flightTimers.push(setTimeout(() => {
    box.classList.add("is-gone");
    onLanded();
    flightTimers.push(setTimeout(() => { if (box.parentNode) box.parentNode.removeChild(box); }, dissolveMs));
  }, liftMs));
}
