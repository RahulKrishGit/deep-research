"use client";
import { useEffect, useState, type ReactNode } from "react";
import { ApiError, ApiUnreachableError, evidenceMarkdownUrl, getEvidence, getReport, reportUrl, type EvidenceResponse, type ResearchSessionResponse } from "@/lib/api";
import { fmtClock, fmtSeconds, passText, qFitClass, toSessionView } from "@/lib/format";
import { useConsole } from "./ConsoleProvider";
import { EvidenceView } from "./EvidenceView";
import { ReportBody } from "./ReportBody";
import { ReportRail } from "./ReportRail";

type Loaded<T> = { kind: "loading" } | { kind: "ready"; value: T } | { kind: "unavailable" };

export function ReportStage({ sessionId, status, strip, passes }: { sessionId: string; status: ResearchSessionResponse; strip: ReactNode; passes: number | null }) {
  const { noteUnreachable, clearUnreachable } = useConsole();
  const [view, setView] = useState<"report" | "evidence">("report");
  const [report, setReport] = useState<Loaded<string>>({ kind: "loading" });
  const [evidence, setEvidence] = useState<Loaded<EvidenceResponse>>({ kind: "loading" });
  useEffect(() => {
    let live = true;
    const unavailable = (e: unknown) => e instanceof ApiError && e.status === 409;
    // K14: an unreachable service raises the console's banner (spec §4.4 "API unreachable") with
    // a Retry that repeats this same read, instead of leaving the card stuck on "loading report".
    const fetchReport = () => {
      getReport(sessionId)
        .then((r) => { if (!live) return; setReport({ kind: "ready", value: r.data }); clearUnreachable(); })
        .catch((e) => {
          if (!live) return;
          if (unavailable(e)) setReport({ kind: "unavailable" });
          else if (e instanceof ApiUnreachableError) noteUnreachable(e.target, fetchReport);
        });
    };
    const fetchEvidence = () => {
      getEvidence(sessionId)
        .then((r) => { if (!live) return; setEvidence({ kind: "ready", value: r.data }); clearUnreachable(); })
        .catch((e) => {
          if (!live) return;
          if (unavailable(e)) setEvidence({ kind: "unavailable" });
          else if (e instanceof ApiUnreachableError) noteUnreachable(e.target, fetchEvidence);
        });
    };
    fetchReport();
    fetchEvidence();
    return () => { live = false; };
  }, [sessionId, noteUnreachable, clearUnreachable]);
  const sv = toSessionView(status, passes);
  const dur = fmtSeconds(status.duration_seconds);
  const meta = `session ${status.session_id} · finished ${fmtClock(status.finished_at) ?? "not recorded"}${dur ? ` · ${dur}` : ""} · ${passText(sv)}`;
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
            {report.kind === "ready" ? <ReportBody markdown={report.value} evidenceLoaded={evidenceLoaded} onOpenEvidence={() => setView("evidence")} />
              : <article className="card"><p className="avail">{report.kind === "unavailable" ? "Not published" : "loading report"}</p></article>}
          </div>
          <ReportRail status={status} evidence={evidenceLoaded ? evidence.value : null} passes={passes} />
        </div>
      ) : evidenceLoaded ? <EvidenceView evidence={evidence.value} />
        : <div className="with-rail"><p className="avail" id="evEmpty">{evidence.kind === "unavailable" ? "Not published" : "loading evidence log"}</p></div>}
    </section>
  );
}
