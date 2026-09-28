// StatusChip.tsx — chipHTML (index.html:1759-1764): one chip, in the topbar, no large variant.
"use client";
import { STATUS, statusNote, type SessionView } from "@/lib/format";
export function StatusChip({ view }: { view: SessionView }) {
  const m = STATUS[view.status] ?? STATUS.running;
  return (
    <span className="chip"><span className={`dot ${m.dot}`} aria-hidden="true"></span>{m.label} <span className="kind">· {statusNote(view)}</span></span>
  );
}
