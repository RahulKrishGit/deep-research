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
