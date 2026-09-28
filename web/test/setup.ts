// @testing-library/react auto-registers `afterEach(cleanup)` only when it finds a global
// `afterEach` at import time; this project imports test hooks from "vitest" per file rather than
// enabling vitest's `globals`, so that check never fires. Registered explicitly here instead —
// otherwise a component rendered in one test (e.g. Composer's alert span) is still in the DOM for
// the next `it()` in the same file (the first component tests, Task 14, are what surfaces this).
import { afterEach } from "vitest";
import { cleanup } from "@testing-library/react";
afterEach(() => cleanup());

// Vitest setup: jsdom lacks window.matchMedia and ResizeObserver. The app reads matchMedia for the
// ≤ 1080 px drawer; tests run at "desktop" (matches: false).
if (typeof window !== "undefined") {
  if (typeof window.matchMedia !== "function") {
    window.matchMedia = (query: string): MediaQueryList => ({
      matches: false, media: query, onchange: null,
      addListener() {}, removeListener() {}, addEventListener() {}, removeEventListener() {},
      dispatchEvent() { return false; },
    });
  }
  if (typeof (window as unknown as { ResizeObserver?: unknown }).ResizeObserver !== "function") {
    (window as unknown as { ResizeObserver: unknown }).ResizeObserver = class { observe() {} unobserve() {} disconnect() {} };
  }
}
