"use client";
import { useEffect, useLayoutEffect, useState, type ReactNode } from "react";
import { ApiUnreachableError, evidenceMarkdownUrl, getEvidence, getReport, reportUrl, type EvidenceResponse, type ResearchSessionResponse } from "@/lib/api";
import { fmtClock, fmtSeconds, passFact, qFitClass } from "@/lib/format";
import { runReportSlide, takeRunningLayout } from "@/lib/handoff";
import { useConsole } from "./ConsoleProvider";
import { EvidenceView } from "./EvidenceView";
import { ReportBody } from "./ReportBody";
import { ReportRail } from "./ReportRail";

type Loaded<T> = { kind: "loading" } | { kind: "ready"; value: T } | { kind: "unavailable" };

export function ReportStage({ sessionId, status, strip }: { sessionId: string; status: ResearchSessionResponse; strip: ReactNode }) {
  const { noteUnreachable, clearUnreachable } = useConsole();
  const [view, setView] = useState<"report" | "evidence">("report");
  const [report, setReport] = useState<Loaded<string>>({ kind: "loading" });
  const [evidence, setEvidence] = useState<Loaded<EvidenceResponse>>({ kind: "loading" });
  useEffect(() => {
    let live = true;
    // C1: the provider now owns the "run every registered retry, clear the banner once every key
    // clears" logic (ConsoleProvider.tsx) — this only has to register its own two keys and clear
    // each one for itself. A single shared { target, retry } pair used to mean whichever read
    // failed *last* silently dropped the other read's retry forever; two independent keys survive
    // the usual outage where both fail at mount.
    const fetchReport = () => {
      getReport(sessionId)
        .then((r) => { if (!live) return; setReport({ kind: "ready", value: r.data }); clearUnreachable("report"); })
        .catch((e) => {
          if (!live) return;
          if (e instanceof ApiUnreachableError) { noteUnreachable("report", e.target, fetchReport); return; }
          setReport({ kind: "unavailable" }); // minor 4: any other ApiError (409 or otherwise) → "Not published", never endless loading
          clearUnreachable("report");
        });
    };
    const fetchEvidence = () => {
      getEvidence(sessionId)
        .then((r) => { if (!live) return; setEvidence({ kind: "ready", value: r.data }); clearUnreachable("evidence"); })
        .catch((e) => {
          if (!live) return;
          if (e instanceof ApiUnreachableError) { noteUnreachable("evidence", e.target, fetchEvidence); return; }
          setEvidence({ kind: "unavailable" });
          clearUnreachable("evidence");
        });
    };
    fetchReport();
    fetchEvidence();
    return () => {
      live = false;
      // NB1: without this, a report/evidence read stuck on ApiUnreachableError leaves its key
      // registered forever once the user navigates away — Retry can never reach it again (there
      // is no more ReportStage to run it), so the banner would stay up until an unrelated key
      // happened to clear too.
      clearUnreachable("report");
      clearUnreachable("evidence");
    };
  }, [sessionId, noteUnreachable, clearUnreachable]);
  /* enterReport, ported (index.html:3195-3231, DESIGN.md:1453-1483 "the header block slides
     down"): the running stage's own rects — noted while it was mounted, RunningPipeline.tsx —
     are consumed once and paired with this stage's own #report-h/#reportOpts. Opening a finished
     session straight from the sidebar never rendered RunningPipeline for it in this page load, so
     takeRunningLayout returns null and nothing slides. */
  useLayoutEffect(() => {
    const layout = takeRunningLayout(sessionId);
    if (!layout) return;
    const q = document.getElementById("report-h");
    const o = document.getElementById("reportOpts");
    if (!q || !o) return;
    runReportSlide([{ from: layout.question, toEl: q }, { from: layout.opts, toEl: o }]);
  }, [sessionId]);
  const dur = fmtSeconds(status.duration_seconds);
  const meta = `session ${status.session_id} · finished ${fmtClock(status.finished_at) ?? "not recorded"}${dur ? ` · ${dur}` : ""} · ${passFact(status.iteration, status.note_passes ?? 0)}`;
  const evidenceLoaded = evidence.kind === "ready";
  return (
    <section className="stage is-on" id="stage-report" data-view={view} aria-labelledby="report-h">
      <div className="report-head">
        <div className="report-head-bar">
          <p className="cap" id="reportMeta">{meta}</p>
          <div className="seg seg-view" role="group" aria-label="Report view" id="segView">
            <button type="button" data-view="report" aria-pressed={view === "report"} onClick={() => setView("report")}>Report</button>
            <button type="button" data-view="evidence" aria-pressed={view === "evidence"} onClick={() => setView("evidence")}>Evidence</button>
          </div>
          <div className="row wrap">
            {report.kind === "ready" ? <a className="btn btn-primary" href={reportUrl(sessionId)} download={`report-${sessionId}.md`} id="downloadBtn">Download Report</a> : null}
            {evidenceLoaded ? <a className="btn btn-ghost" href={evidenceMarkdownUrl(sessionId)} download={`report-${sessionId}-evidence.md`} id="downloadEvidenceBtn">Download evidence log</a> : null}
            {status.trace_url ? <a className="btn btn-ghost" href={status.trace_url} target="_blank" rel="noopener" id="traceBtn">Open LangSmith trace <span aria-hidden="true">↗</span></a> : null}
          </div>
        </div>
        {/* Controller ruling 1: #report-h follows the Task 16 q-center pattern and the prototype's
            Q_HOSTS/CSS (index.html:372, :1798) — the base class is report-q, as the prototype's own
            #report-h markup uses (":1362"), not ask-q/ask-locked (those style the other stages' h1). */}
        <h1 className={"report-q" + qFitClass(status.query)} id="report-h">{status.query}</h1>
        {strip}
      </div>
      {view === "report" ? (
        <div className="with-rail report-main">
          <div className="stack" style={{ gap: "var(--space-6)" }}>
            {report.kind === "ready" ? <ReportBody markdown={report.value} evidenceLoaded={evidenceLoaded} onOpenEvidence={() => setView("evidence")} notes={status.notes ?? []} />
              : <article className="card"><p className="avail">{report.kind === "unavailable" ? "Not published" : "loading report"}</p></article>}
          </div>
          <ReportRail status={status} evidence={evidenceLoaded ? evidence.value : null} />
        </div>
      ) : evidenceLoaded ? <EvidenceView evidence={evidence.value} />
        : <div className="with-rail"><p className="avail" id="evEmpty">{evidence.kind === "unavailable" ? "Not published" : "loading evidence log"}</p></div>}
    </section>
  );
}
