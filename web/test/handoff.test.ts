// The two DESIGN.md handoffs' state machines (DESIGN.md §5.6, "The two handoffs"; prototype clearFlight,
// flyQuestionToLock, holdBeat, REPORT_HANDOFF/enterReport). jsdom has no layout
// engine (every rect is 0x0x0x0), so these tests cover the state machine — session matching,
// one-shot consumption, cancellation, graceful degradation with no usable geometry — not pixel
// animation, which e2e/handoff.spec.ts proves against a real browser.
import { afterEach, describe, expect, it, vi } from "vitest";
import {
  armIdleToRunningFlight, beginIdleToRunningClear, cancelDeferredClearIdleToRunningFlight, cancelIdleToRunningClear,
  clearIdleToRunningFlight, clearRunningLayout, deferClearIdleToRunningFlight, motionMs, noteRunningLayout, reducedMotion,
  runIdleToRunningLift, runReportSlide, takeIdleToRunningFlight, takeRunningLayout,
} from "../lib/handoff";

afterEach(() => {
  clearIdleToRunningFlight();
  clearRunningLayout();
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
  it("never creates a .q-flight box under prefers-reduced-motion, even with real geometry (DESIGN.md §5.6)", async () => {
    vi.stubGlobal("matchMedia", (q: string) => ({ matches: true, media: q }) as MediaQueryList);
    document.body.innerHTML = '<form id="composer"></form>';
    const composer = document.getElementById("composer")!;
    vi.spyOn(composer, "getBoundingClientRect").mockReturnValue({ left: 10, top: 20, right: 110, bottom: 70, width: 100, height: 50, x: 10, y: 20, toJSON() { return {}; } });
    await armIdleToRunningFlight({ sessionId: "s1", question: "q", composerEl: composer, clearDeadline: Date.now() });
    expect(document.querySelector(".q-flight")).toBeNull();
    expect(composer.classList.contains("is-handing-off")).toBe(false); // "the composer keeps its own frame"
    const flight = takeIdleToRunningFlight("s1"); // beats one and three still run — the flight is still recorded, just boxless
    expect(flight?.box ?? null).toBeNull();
  });
  describe("deferClearIdleToRunningFlight / cancelDeferredClearIdleToRunningFlight (React StrictMode dev)", () => {
    // next.config.ts's reactStrictMode:true mounts, cleans up and remounts every effect
    // synchronously in `next dev` — an unmount cleanup that clears the flight immediately would
    // kill it before the remounted effect ever runs. Deferring by one tick and cancelling if the
    // same session's effect fires again (the remount) lets a real unmount/navigation still clear.
    it("clears after the deferred tick when nothing cancels it (a real unmount/navigation)", async () => {
      document.body.innerHTML = '<form id="composer"></form>';
      await armIdleToRunningFlight({ sessionId: "s1", question: "q", composerEl: document.getElementById("composer"), clearDeadline: Date.now() });
      deferClearIdleToRunningFlight("s1");
      expect(takeIdleToRunningFlight("s1"), "not cleared synchronously").not.toBeNull();
    });
    it("cancelling before the deferred tick keeps the flight intact (a StrictMode remount)", async () => {
      vi.useFakeTimers();
      document.body.innerHTML = '<form id="composer"></form>';
      await armIdleToRunningFlight({ sessionId: "s1", question: "q", composerEl: document.getElementById("composer"), clearDeadline: Date.now() });
      deferClearIdleToRunningFlight("s1");
      cancelDeferredClearIdleToRunningFlight("s1"); // the "remount": same session, cancels the pending clear
      await vi.advanceTimersByTimeAsync(10);
      expect(takeIdleToRunningFlight("s1"), "the deferred clear must never have fired").not.toBeNull();
      vi.useRealTimers();
    });
    it("not cancelling lets the deferred clear fire after the tick", async () => {
      vi.useFakeTimers();
      document.body.innerHTML = '<form id="composer"></form>';
      await armIdleToRunningFlight({ sessionId: "s1", question: "q", composerEl: document.getElementById("composer"), clearDeadline: Date.now() });
      deferClearIdleToRunningFlight("s1");
      await vi.advanceTimersByTimeAsync(10);
      expect(takeIdleToRunningFlight("s1")).toBeNull();
      vi.useRealTimers();
    });
    it("cancelling a different session's id never cancels this session's deferred clear", async () => {
      vi.useFakeTimers();
      document.body.innerHTML = '<form id="composer"></form>';
      await armIdleToRunningFlight({ sessionId: "s1", question: "q", composerEl: document.getElementById("composer"), clearDeadline: Date.now() });
      deferClearIdleToRunningFlight("s1");
      cancelDeferredClearIdleToRunningFlight("s2"); // a different session's remount — must not save s1's flight
      await vi.advanceTimersByTimeAsync(10);
      expect(takeIdleToRunningFlight("s1")).toBeNull();
      vi.useRealTimers();
    });
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

describe("running → report layout handoff (REPORT_HANDOFF/enterReport)", () => {
  it("takeRunningLayout returns the noted rects for a matching session, one-shot", () => {
    document.body.innerHTML = '<h1 id="running-h"></h1><div id="runningOpts"></div>';
    noteRunningLayout("s1", document.getElementById("running-h")!, document.getElementById("runningOpts")!);
    expect(takeRunningLayout("s1")).not.toBeNull();
    expect(takeRunningLayout("s1")).toBeNull(); // consumed
  });
  it("a different session's rects are never handed over — opening a finished session from the sidebar must not slide", () => {
    document.body.innerHTML = '<h1 id="running-h"></h1><div id="runningOpts"></div>';
    noteRunningLayout("s1", document.getElementById("running-h")!, document.getElementById("runningOpts")!);
    expect(takeRunningLayout("s2")).toBeNull();
  });
  it("no prior noteRunningLayout call (a session opened straight to its report) returns null", () => {
    expect(takeRunningLayout("s1")).toBeNull();
  });
});

describe("runReportSlide", () => {
  function mockRect(el: HTMLElement, top: number, height = 10) {
    vi.spyOn(el, "getBoundingClientRect").mockReturnValue({ left: 0, top, right: 10, bottom: top + height, width: 10, height, x: 0, y: top, toJSON() { return {}; } });
  }
  it("resets a stale inline transform/transition on every call before doing anything else", () => {
    document.body.innerHTML = '<div id="q"></div>';
    const el = document.getElementById("q")!;
    el.style.transform = "translateY(-40px)";
    el.style.transition = "transform 999ms linear";
    mockRect(el, 300);
    runReportSlide([{ from: null, toEl: el }]); // no `from`: nothing to animate, but the reset still runs
    expect(el.style.transform).toBe("");
    expect(el.style.transition).toBe("");
  });
  it("a pair less than 2px apart is left alone (the two already agree)", () => {
    document.body.innerHTML = '<div id="q"></div>';
    const el = document.getElementById("q")!;
    mockRect(el, 300);
    runReportSlide([{ from: { left: 0, top: 301, width: 10, height: 10 }, toEl: el }]);
    expect(el.style.transform).toBe("");
    expect(el.style.transition).toBe("");
  });
  it("applies the inverse offset then releases it into a transition, clearing both after --motion-base", () => {
    vi.useFakeTimers();
    document.body.innerHTML = '<div id="q"></div>';
    const el = document.getElementById("q")!;
    mockRect(el, 300);
    runReportSlide([{ from: { left: 0, top: 100, width: 10, height: 10 }, toEl: el }]);
    // Released synchronously within the same call (the forced reflow is what lets the browser
    // still animate from the offset it never got to paint) — by the time this returns, the
    // transform is already back to none and a transition is what carries it there.
    expect(el.style.transform).toBe("");
    expect(el.style.transition).toContain("transform");
    vi.advanceTimersByTime(motionMs("--motion-base", 200) + 40);
    expect(el.style.transition).toBe("");
    vi.useRealTimers();
  });
  it("never inherits a half-finished slide from a previous one: cancels the prior clear-timer on every call", () => {
    vi.useFakeTimers();
    document.body.innerHTML = '<div id="q1"></div><div id="q2"></div>';
    const el1 = document.getElementById("q1")!;
    const el2 = document.getElementById("q2")!;
    mockRect(el1, 300);
    mockRect(el2, 300);
    runReportSlide([{ from: { left: 0, top: 100, width: 10, height: 10 }, toEl: el1 }]);
    vi.advanceTimersByTime(motionMs("--motion-base", 200) + 40); // el1's clear-timer fires and resolves
    runReportSlide([{ from: { left: 0, top: 150, width: 10, height: 10 }, toEl: el2 }]);
    expect(el2.style.transition).toContain("transform");
    vi.advanceTimersByTime(motionMs("--motion-base", 200) + 40);
    expect(el2.style.transition).toBe("");
    vi.useRealTimers();
  });
  it("resets a stale transform but applies no new one under prefers-reduced-motion, even with real geometry (DESIGN.md §5.6)", () => {
    vi.stubGlobal("matchMedia", (q: string) => ({ matches: true, media: q }) as MediaQueryList);
    document.body.innerHTML = '<div id="q"></div>';
    const el = document.getElementById("q")!;
    el.style.transform = "translateY(-40px)";
    el.style.transition = "transform 999ms linear";
    mockRect(el, 300);
    runReportSlide([{ from: { left: 0, top: 100, width: 10, height: 10 }, toEl: el }]);
    expect(el.style.transform).toBe("");
    expect(el.style.transition).toBe("");
  });
});
