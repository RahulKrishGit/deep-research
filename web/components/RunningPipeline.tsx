"use client";
import { useEffect, useLayoutEffect, useState, type ReactNode } from "react";
import { fmtElapsed, qFitClass } from "@/lib/format";
import { noteRunningLayout } from "@/lib/handoff";
import { marksFor, type NodeId, type RunState } from "@/lib/run-state";
import { BriefSpine } from "./BriefSpine";

/* live-briefs spec §4.2 (D12, D13, D14): no "Now" header, no counters block and no pass counter —
   the pipeline card holds the spine alone, whose active row is open on its live brief (§4.3). The
   row's accessible name (" (in progress)") and aria-current="step" are the non-colour state signals. */
export function RunningPipeline({ sessionId, run, question, strip, startedAt, onToggleRow }: { sessionId: string; run: RunState; question: string; strip: ReactNode; startedAt: string; onToggleRow(id: NodeId): void }) {
  const [elapsed, setElapsed] = useState(0);
  useEffect(() => {
    const tick = () => setElapsed(Math.max(0, Math.floor((Date.now() - new Date(startedAt).getTime()) / 1000)));
    tick();
    const timer = setInterval(tick, 1000);
    return () => clearInterval(timer);
  }, [startedAt]);
  // Noted on every render while this stage is mounted, so the rects are as fresh as the moment
  // status flips to a terminal one allows — see enterReport/runReportSlide (ReportStage.tsx),
  // ported from REPORT_HANDOFF (index.html:3195) / DESIGN.md:1453-1483.
  useLayoutEffect(() => {
    const q = document.getElementById("running-h");
    const o = document.getElementById("runningOpts");
    if (q && o) noteRunningLayout(sessionId, q, o);
  });
  return (
    // no-enter + is-arriving (index.html:2440, :3797, DESIGN.md:1438-1451 "submitted → running"):
    // both stages share the same header offset inside the same .run-wrap, so the seam is held
    // still — the generic slide is suppressed and only the card below the header rises in.
    <section className="stage is-on no-enter is-arriving" id="stage-running" aria-labelledby="running-h">
      <div className="run-wrap">
        <div className="ask-head">
          <p className="eyebrow" style={{ margin: 0 }}>Session running</p>
          <h1 className={"ask-q ask-locked" + qFitClass(question)} id="running-h" aria-describedby="runningOpts">{question}</h1>
          {strip}
          <div className="ask-meta"><span className="avail-mono" id="runElapsed">{fmtElapsed(elapsed)} elapsed</span></div>
        </div>
        <div className="card stack" style={{ gap: "var(--space-5)" }}>
          <BriefSpine marks={marksFor(run, run.active)} run={run} onToggle={onToggleRow} />
        </div>
      </div>
    </section>
  );
}
