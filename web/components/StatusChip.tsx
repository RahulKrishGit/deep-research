// StatusChip.tsx — chipHTML (index.html:1759-1764): one chip, in the topbar, no large variant.
"use client";
import type { Ref } from "react";
import { STATUS, statusNote, type SessionView } from "@/lib/format";
/* The chip is not a control, but it takes focus by script (tabIndex -1, never a tab stop): when Stop is
   withdrawn while it holds focus, the topbar hands focus here instead of letting it fall to <body>
   (owner decision O2, 2026-10-01). It keeps the app's own :focus-visible style, which draws nothing for a
   press and the ring for a keyboard user. */
export function StatusChip({ view, ref }: { view: SessionView; ref?: Ref<HTMLSpanElement> }) {
  const m = STATUS[view.status] ?? STATUS.running;
  return (
    <span className="chip" tabIndex={-1} ref={ref}><span className={`dot ${m.dot}`} aria-hidden="true"></span>{m.label} <span className="kind">· {statusNote(view)}</span></span>
  );
}
