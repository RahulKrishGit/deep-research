// notes-progress-report spec §6.5: a ticker's visible sample changes at most once every
// TICKER_HOLD_MS. A dwell, like HANDOFF_HOLD_MS (components/BriefSpine.tsx), not an animation
// duration, so it holds under reduced motion too. A sample that arrives sooner waits; when the hold
// ends the newest waiting sample shows, so a burst and a replay end on the same sample.
import { useEffect, useRef, useState } from "react";

export const TICKER_HOLD_MS = 1200;

export interface Ticked<T> { current: T | null; previous: T | null }

/* The sample to show now and the one it replaced (which fades out under it). The first sample
   shows at once; each later one shows TICKER_HOLD_MS after the one before it, or at once when that
   long has already passed. */
export function useTicker<T extends { key: string }>(latest: T | null): Ticked<T> {
  const [shown, setShown] = useState<Ticked<T>>({ current: latest, previous: null });
  const since = useRef<number>(Date.now());
  useEffect(() => {
    if (latest === null || latest.key === shown.current?.key) return;
    const show = () => {
      since.current = Date.now();
      setShown((s) => ({ current: latest, previous: s.current }));
    };
    const wait = shown.current === null ? 0 : Math.max(0, TICKER_HOLD_MS - (Date.now() - since.current));
    if (wait === 0) { show(); return; }
    const timer = setTimeout(show, wait);
    return () => clearTimeout(timer);
  }, [latest, shown]);
  return shown;
}
