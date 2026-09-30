import { act, render } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { TWEEN_MS, tweenValue, useTween } from "../lib/tween";

afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); });

function Shown({ value }: { value: number }) { return <span>{useTween(value)}</span>; }

describe("count tweens (live-briefs spec §4.3: numbers tween over 400 ms)", () => {
  it("eases from the old value to the new one and lands exactly at 400 ms", () => {
    expect(TWEEN_MS).toBe(400);
    expect(tweenValue(10, 20, 0)).toBe(10);
    expect(tweenValue(10, 20, 200)).toBe(19); // ease-out cubic: 1 - 0.5^3 = 0.875
    expect(tweenValue(10, 20, 399)).toBe(20);
    expect(tweenValue(10, 20, 400)).toBe(20);
    expect(tweenValue(10, 20, -5)).toBe(10);
  });
  it("counts through the values frame by frame", () => {
    vi.useFakeTimers();
    vi.stubGlobal("requestAnimationFrame", (cb: FrameRequestCallback) => setTimeout(() => cb(0), 16) as unknown as number);
    vi.stubGlobal("cancelAnimationFrame", (id: number) => clearTimeout(id));
    const { container, rerender } = render(<Shown value={2} />);
    rerender(<Shown value={40} />);
    expect(container.textContent).toBe("2");
    act(() => { vi.advanceTimersByTime(96); });
    const mid = Number(container.textContent);
    expect(mid).toBeGreaterThan(2);
    expect(mid).toBeLessThan(40);
    act(() => { vi.advanceTimersByTime(TWEEN_MS); });
    expect(container.textContent).toBe("40");
  });
  it("jumps from nothing, to zero and under reduced motion", () => {
    const { container, rerender } = render(<Shown value={0} />);
    rerender(<Shown value={3} />);
    expect(container.textContent).toBe("3");
    rerender(<Shown value={0} />);
    expect(container.textContent).toBe("0");
    vi.stubGlobal("matchMedia", (query: string) => ({ matches: query.includes("reduce"), media: query, onchange: null, addListener() {}, removeListener() {}, addEventListener() {}, removeEventListener() {}, dispatchEvent() { return false; } }));
    rerender(<Shown value={5} />);
    rerender(<Shown value={9} />);
    expect(container.textContent).toBe("9");
  });
});
