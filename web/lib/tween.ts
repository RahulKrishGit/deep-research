// Counts tween from the old value to the new one over 400 ms (live-briefs spec §4.3 motion table);
// under reduced motion they jump. A value that appears from nothing, or reaches or leaves 0, jumps
// too: its words change ("no findings" ↔ "1 finding"), so there is nothing to count through.
import { useEffect, useRef, useState } from "react";
import { reducedMotion } from "./handoff";

export const TWEEN_MS = 400;

/* The integer to show `elapsedMs` into a tween from `from` to `to` (ease-out cubic). */
export function tweenValue(from: number, to: number, elapsedMs: number, durationMs: number = TWEEN_MS): number {
  if (elapsedMs >= durationMs) return to;
  const t = Math.max(0, elapsedMs) / durationMs;
  const eased = 1 - Math.pow(1 - t, 3);
  return Math.round(from + (to - from) * eased);
}

export function useTween(value: number): number {
  const [shown, setShown] = useState(value);
  const current = useRef(value);
  useEffect(() => {
    const from = current.current;
    if (from === value) return;
    if (from <= 0 || value <= 0 || reducedMotion()) { current.current = value; setShown(value); return; }
    const start = Date.now();
    let frame = 0;
    const step = () => {
      const next = tweenValue(from, value, Date.now() - start);
      current.current = next;
      setShown(next);
      if (next !== value) frame = requestAnimationFrame(step);
    };
    frame = requestAnimationFrame(step);
    return () => cancelAnimationFrame(frame);
  }, [value]);
  return shown;
}
