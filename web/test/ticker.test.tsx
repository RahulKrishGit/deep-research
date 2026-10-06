import { act, renderHook } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { TICKER_HOLD_MS, useTicker } from "../lib/ticker";

afterEach(() => { vi.useRealTimers(); });

type Sample = { key: string };
const s = (key: string): Sample => ({ key });
const mount = (latest: Sample | null) =>
  renderHook(({ latest: l }: { latest: Sample | null }) => useTicker(l), { initialProps: { latest } });

describe("useTicker (at most one sample per 1,200 ms)", () => {
  it("holds for 1,200 ms", () => { expect(TICKER_HOLD_MS).toBe(1200); });
  it("shows the first sample at once and holds the next until 1,200 ms after it", () => {
    vi.useFakeTimers();
    const { result, rerender } = mount(null);
    expect(result.current).toEqual({ current: null, previous: null });
    rerender({ latest: s("a") });
    expect(result.current).toEqual({ current: s("a"), previous: null });
    act(() => { vi.advanceTimersByTime(300); });
    rerender({ latest: s("b") });
    expect(result.current.current).toEqual(s("a"));
    act(() => { vi.advanceTimersByTime(TICKER_HOLD_MS - 301); });
    expect(result.current.current).toEqual(s("a"));
    act(() => { vi.advanceTimersByTime(1); });
    expect(result.current).toEqual({ current: s("b"), previous: s("a") });
  });
  it("ends a burst on its newest sample, as a replay would", () => {
    vi.useFakeTimers();
    const { result, rerender } = mount(s("a"));
    rerender({ latest: s("b") });
    rerender({ latest: s("c") });
    rerender({ latest: s("d") });
    expect(result.current.current).toEqual(s("a"));
    act(() => { vi.advanceTimersByTime(TICKER_HOLD_MS); });
    expect(result.current).toEqual({ current: s("d"), previous: s("a") });
  });
  it("shows a sample at once when the hold has already passed", () => {
    vi.useFakeTimers();
    const { result, rerender } = mount(s("a"));
    act(() => { vi.advanceTimersByTime(TICKER_HOLD_MS + 500); });
    rerender({ latest: s("b") });
    expect(result.current).toEqual({ current: s("b"), previous: s("a") });
  });
  it("clears when the latest sample goes away (a re-armed step), and shows the next one at once", () => {
    vi.useFakeTimers();
    const { result, rerender } = mount(s("a"));
    rerender({ latest: null });
    expect(result.current).toEqual({ current: null, previous: null });
    rerender({ latest: s("b") });
    expect(result.current).toEqual({ current: s("b"), previous: null });
  });
  it("shows a key it showed before the clear again: the new pass restarts its numbering", () => {
    vi.useFakeTimers();
    const { result, rerender } = mount(s("v1"));
    act(() => { vi.advanceTimersByTime(TICKER_HOLD_MS); });
    rerender({ latest: s("v2") });
    expect(result.current).toEqual({ current: s("v2"), previous: s("v1") });
    rerender({ latest: null });
    rerender({ latest: s("v1") });
    expect(result.current).toEqual({ current: s("v1"), previous: null });
  });
});
