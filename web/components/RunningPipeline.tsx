"use client";
import { useEffect, useLayoutEffect, useState, type ReactNode } from "react";
import { fmtElapsed, passText, qFitClass } from "@/lib/format";
import { noteRunningLayout } from "@/lib/handoff";
import { AGENT_ORDER, BLURB, STAGES, marksFor, type RunState } from "@/lib/run-state";
import { Counters } from "./Counters";
import { Spine } from "./Spine";

export function RunningPipeline({ sessionId, run, question, strip, startedAt, ceiling }: { sessionId: string; run: RunState; question: string; strip: ReactNode; startedAt: string; ceiling: number | null }) {
  const [elapsed, setElapsed] = useState(0);
  useEffect(() => {
    const tick = () => setElapsed(Math.max(0, Math.floor((Date.now() - new Date(startedAt).getTime()) / 1000)));
    tick();
    const timer = setInterval(tick, 1000);
    return () => clearInterval(timer);
  }, [startedAt]);
  let idx = run.active ? AGENT_ORDER.indexOf(run.active) : STAGES.length - 1;
  if (idx < 0) idx = 0;
  const stage = STAGES[idx];
  /* K15: run.maxPasses defaults to 1 until the settings record or graph.session.started arrives,
     which is not a known ceiling of 1 — passText drops the "of P" clause for a null ceiling, so
     the caller's own ceiling (never run.maxPasses) decides whether the clause prints. */
  const passesLabel = passText({ status: "running", iteration: run.pass - 1, passes: ceiling, review: null, coverage: null });
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
          <div className="pipe-now">
            <div>
              <span className="cap">Now</span>
              <div className="now-stage" id="runNow">{stage.label}</div>
              <p className="sm" id="runningBlurb">{run.blurbs[stage.id] || BLURB[stage.id]}</p>
              <p className="loop-tag" id="runLoopTag" hidden={!run.tag} {...(run.tag ? { "data-kind": run.tag.kind } : {})}>
                <span className="tag">{run.tag?.label ?? ""}</span><span className="why">{run.tag?.text ?? ""}</span>
              </p>
            </div>
            <div className="row-between">
              <span className="avail-mono" id="runProgressLabel">stage {idx + 1} of {STAGES.length}</span>
              <span className="avail-mono" id="runPasses">{passesLabel}</span>
            </div>
            <span className="track" id="runTrack" role="progressbar" aria-label="Pipeline progress" aria-valuemin={1} aria-valuemax={7} aria-valuenow={idx + 1}>
              <span id="runTrackFill" style={{ width: `${Math.round(((idx + 1) / STAGES.length) * 100)}%` }}></span>
            </span>
          </div>
          <Spine marks={marksFor(run, run.active)} run={run} withArcs />
          <Counters counters={run.counters} absentText="not yet" pass={run.countersPass} />
        </div>
      </div>
    </section>
  );
}
