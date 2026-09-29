// The idle→running lift's state machine (DESIGN.md:1359-1451; prototype clearFlight :1941,
// flyQuestionToLock :2009, holdBeat :2069). jsdom has no layout engine (every rect is 0x0x0x0), so
// these tests cover the state machine — session matching, one-shot consumption, cancellation,
// graceful degradation with no usable geometry — not pixel animation, which e2e/handoff.spec.ts
// proves against a real browser.
import { afterEach, describe, expect, it, vi } from "vitest";
import {
  armIdleToRunningFlight, beginIdleToRunningClear, cancelIdleToRunningClear, clearIdleToRunningFlight,
  motionMs, reducedMotion, runIdleToRunningLift, takeIdleToRunningFlight,
} from "../lib/handoff";

afterEach(() => {
  clearIdleToRunningFlight();
  vi.unstubAllGlobals();
  document.body.innerHTML = "";
});

describe("motionMs / reducedMotion", () => {
  it("falls back to the given default when the CSS custom property is not defined (no stylesheet loaded)", () => {
    expect(motionMs("--motion-lift", 900)).toBe(900);
    expect(motionMs("--motion-clear", 320)).toBe(320);
  });
  it("parses a plain millisecond value", () => {
    document.documentElement.style.setProperty("--motion-lift", "900ms");
    expect(motionMs("--motion-lift", 1)).toBe(900);
    document.documentElement.style.removeProperty("--motion-lift");
  });
  it("parses a seconds value — Next's production build minifies 900ms to .9s, CSSOM's own canonical form for a <1s time, which getComputedStyle hands back verbatim", () => {
    document.documentElement.style.setProperty("--motion-lift", ".9s");
    expect(motionMs("--motion-lift", 1)).toBe(900);
    document.documentElement.style.removeProperty("--motion-lift");
  });
  it("reads prefers-reduced-motion from matchMedia", () => {
    vi.stubGlobal("matchMedia", (q: string) => ({ matches: true, media: q }) as MediaQueryList);
    expect(reducedMotion()).toBe(true);
  });
});

describe("beginIdleToRunningClear / cancelIdleToRunningClear", () => {
  it("adds is-clearing to the named stage and returns a deadline --motion-clear ahead of now", () => {
    document.body.innerHTML = '<section id="stage-idle"></section>';
    const before = Date.now();
    const deadline = beginIdleToRunningClear("stage-idle");
    expect(document.getElementById("stage-idle")!.classList.contains("is-clearing")).toBe(true);
    expect(deadline).toBeGreaterThanOrEqual(before + 320);
  });
  it("cancelIdleToRunningClear removes is-clearing and drops any pending flight — a failed POST restores the composer intact", async () => {
    document.body.innerHTML = '<section id="stage-idle"></section><form id="composer"></form>';
    const clearDeadline = beginIdleToRunningClear("stage-idle");
    await armIdleToRunningFlight({ sessionId: "s1", question: "q", composerEl: document.getElementById("composer"), clearDeadline: Date.now() });
    void clearDeadline;
    cancelIdleToRunningClear("stage-idle");
    expect(document.getElementById("stage-idle")!.classList.contains("is-clearing")).toBe(false);
    expect(takeIdleToRunningFlight("s1")).toBeNull();
  });
});

describe("armIdleToRunningFlight / takeIdleToRunningFlight", () => {
  it("hands the flight to the session it was armed for, exactly once", async () => {
    document.body.innerHTML = '<form id="composer"></form>';
    await armIdleToRunningFlight({ sessionId: "s1", question: "the question", composerEl: document.getElementById("composer"), clearDeadline: Date.now() });
    const flight = takeIdleToRunningFlight("s1");
    expect(flight?.question).toBe("the question");
    expect(flight?.sessionId).toBe("s1");
    expect(takeIdleToRunningFlight("s1")).toBeNull(); // one-shot: already consumed
  });
  it("a flight armed for a different session is cancelled outright, never dropped onto the wrong page", async () => {
    document.body.innerHTML = '<form id="composer"></form>';
    await armIdleToRunningFlight({ sessionId: "s1", question: "q", composerEl: document.getElementById("composer"), clearDeadline: Date.now() });
    expect(takeIdleToRunningFlight("s2")).toBeNull(); // wrong session: nothing handed over
    expect(takeIdleToRunningFlight("s1")).toBeNull(); // and s1's own flight was cancelled, not left dangling for a later call
  });
  it("waits out the remainder of the clear deadline before resolving", async () => {
    document.body.innerHTML = '<form id="composer"></form>';
    vi.useFakeTimers();
    const clearDeadline = Date.now() + 250;
    let resolved = false;
    const p = armIdleToRunningFlight({ sessionId: "s1", question: "q", composerEl: document.getElementById("composer"), clearDeadline }).then(() => { resolved = true; });
    await vi.advanceTimersByTimeAsync(100);
    expect(resolved).toBe(false);
    await vi.advanceTimersByTimeAsync(200);
    await p;
    expect(resolved).toBe(true);
    vi.useRealTimers();
  });
});

describe("runIdleToRunningLift — no usable geometry (jsdom, or a collapsed frame)", () => {
  it("still calls onLanded and leaves no .q-flight in the DOM", async () => {
    document.body.innerHTML = '<h1 id="submitted-h"></h1>';
    const flight = { sessionId: "s1", question: "q", from: { left: 0, top: 0, width: 0, height: 0 }, box: null };
    const onLanded = vi.fn();
    runIdleToRunningLift(flight, document.getElementById("submitted-h")!, onLanded);
    await vi.waitFor(() => expect(onLanded).toHaveBeenCalledTimes(1));
    expect(document.querySelector(".q-flight")).toBeNull();
  });
});
