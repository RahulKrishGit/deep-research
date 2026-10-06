"use client";
import { useEffect, useLayoutEffect, useState, type ReactNode } from "react";
import { fmtElapsed, qFitClass } from "@/lib/format";
import { noteRunningLayout } from "@/lib/handoff";
import { notesLeft } from "@/lib/notes";
import { marksFor, type NodeId, type RunState } from "@/lib/run-state";
import { BriefSpine } from "./BriefSpine";
import { NoteLine } from "./NoteLine";

/* No "Now" header, no counters block and no pass counter —
   the pipeline card holds the spine alone, whose active row is open on its live brief. The
   row's accessible name (" (in progress)") and aria-current="step" are the non-colour state signals. */
/* The note line is the card's last element; `notesRemaining` is the last
   /status's count, lowered by every note the stream has received since (lib/notes.ts notesLeft). */
export function RunningPipeline({ sessionId, run, question, strip, startedAt, onToggleRow, notesRemaining }: { sessionId: string; run: RunState; question: string; strip: ReactNode; startedAt: string; onToggleRow(id: NodeId): void; notesRemaining?: number }) {
  const [elapsed, setElapsed] = useState(0);
  // The same one-second clock ticks the steps' elapsed times.
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const tick = () => {
      const at = Date.now();
      setNow(at);
      setElapsed(Math.max(0, Math.floor((at - new Date(startedAt).getTime()) / 1000)));
    };
    tick();
    const timer = setInterval(tick, 1000);
    return () => clearInterval(timer);
  }, [startedAt]);
  // Noted on every render while this stage is mounted, so the rects are as fresh as the moment
  // status flips to a terminal one allows — see enterReport/runReportSlide (ReportStage.tsx),
  // ported from the prototype's REPORT_HANDOFF (DESIGN.md §5.6, "Running → report: the header block slides down").
  useLayoutEffect(() => {
    const q = document.getElementById("running-h");
    const o = document.getElementById("runningOpts");
    if (q && o) noteRunningLayout(sessionId, q, o);
  });
  return (
    // no-enter + is-arriving (DESIGN.md §5.6, "Submitted → running"):
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
          <BriefSpine marks={marksFor(run, run.active)} run={run} onToggle={onToggleRow} now={now} />
          <NoteLine sessionId={sessionId} remaining={notesLeft(notesRemaining, run.notes.length)} />
        </div>
      </div>
    </section>
  );
}
