"use client";
import type { ReactNode } from "react";
import type { ResearchError, ResearchSessionResponse } from "@/lib/api";
import { HALT_HEADLINES, fmtClock, qFitClass } from "@/lib/format";
import { AGENT_ORDER, failedMarks, type RunState } from "@/lib/run-state";
import { Counters } from "./Counters";
import { Spine } from "./Spine";

/* Agents record non-recoverable provider failures that a research pass is expected to survive
   (graph/state.py's HALTING_ERROR_TYPES), so the first
   non-recoverable error in `errors` is not necessarily the halt that ended the run. Select, in
   order: (1) the first record whose error_type is a recognised halting type (a HALT_HEADLINES
   key); (2) failing that, the first non-recoverable record; (3) failing that, no record — never
   an invented api.research.failed/api/false stand-in. */
export function FailedStage({ status, run, strip }: { status: ResearchSessionResponse; run: RunState; strip: ReactNode }) {
  const hard: ResearchError | null =
    status.errors.find((e) => e.error_type in HALT_HEADLINES)
    ?? status.errors.find((e) => !e.recoverable)
    ?? null;
  // An unrecognised type still headlines in plain words, never its raw enum value
  // — the raw error_type stays visible in the facts row below.
  const headline = hard ? (HALT_HEADLINES[hard.error_type] ?? "Research run failed") : "Research run failed";
  const reason = hard && typeof hard.details.reason === "string" ? hard.details.reason : null;
  const exceptionType = hard && typeof hard.details.exception_type === "string" ? hard.details.exception_type : null;
  // `_record_failure` (sessions.py) never sets `session.outcome`, so an API-level failure
  // (source "api" — a configuration error or an unhandled exception before the graph ever ran)
  // answers `GET /report` with 409 session_not_complete, the same code a still-running session
  // gets — not report_unavailable, which is reserved for a graph halt that reached an outcome
  // with no report. A record-less halt defaults to the graph wording (nothing invented).
  const apiFailure = hard?.source === "api";
  const reportCode = apiFailure ? "session_not_complete" : "report_unavailable";
  const haltIndex = run.openNode ? AGENT_ORDER.indexOf(run.openNode) : -1;
  return (
    <section className="stage is-on" id="stage-failed" aria-labelledby="failed-h">
      <div className="report-head">
        <div style={{ minWidth: 0 }}>
          <p className="cap" id="failedMeta">session {status.session_id} · finished {fmtClock(status.finished_at) ?? "not recorded"}</p>
          {/* A short question sits centred in the frame via the .q-center class (qFitClass), as on
              #report-h: the base class is report-q, not ask-q/ask-locked, matching the prototype's
              #failed-h markup and its .report-q.q-center CSS. */}
          <h1 className={"report-q" + qFitClass(status.query)} id="failed-h">{status.query}</h1>
          {strip}
        </div>
        <div className="row wrap">
          {status.trace_url ? <a className="btn btn-ghost" href={status.trace_url} target="_blank" rel="noopener" id="failedTrace">Open LangSmith trace <span aria-hidden="true">↗</span></a> : null}
        </div>
      </div>
      <div className="with-rail">
        <div className="stack" style={{ gap: "var(--space-5)" }}>
          <div className="note bad">
            {/* The headline is the halting type in plain words; the enumerated type stays in the facts; the API's message is the sentence. */}
            <div className="note-head"><span className="mk">halt</span><span id="failedType">{headline}</span></div>
            <p id="failedMessage">{hard?.message || "The run stopped on a non-recoverable error."}</p>
          </div>
          <div className="card stack" style={{ gap: "var(--space-4)" }}>
            <h2 className="card-title">Why nothing was published</h2>
            <p className="sm">A halted run skips publication: no report, evidence log or quality record was written.</p>
            <dl className="kv">
              {hard ? <><dt>error_type</dt><dd id="failFactType">{hard.error_type}</dd></> : null}
              {/* No invented values — the source row is omitted rather than showing a made-up "graph". */}
              {hard?.source ? <><dt>source</dt><dd id="failFactSource">{hard.source}</dd></> : null}
              {hard ? <><dt>recoverable</dt><dd id="failFactRecoverable">{String(Boolean(hard.recoverable))}</dd></> : null}
              {reason ? <><dt>reason</dt><dd id="failFactReason">{reason}</dd></> : null}
              {exceptionType ? <><dt>exception_type</dt><dd id="failFactException">{exceptionType}</dd></> : null}
              <dt>report_path</dt><dd className="avail">Not published</dd>
              <dt>GET /report</dt><dd id="failFactReportCode">409 {reportCode}</dd>
            </dl>
            {apiFailure ? (
              <p className="avail">The service never recorded an outcome for a run that fails before publishing, so <span className="mono">GET /report</span> answers <span className="mono">409 session_not_complete</span> — the same code a still-running session returns — distinct from the <span className="mono">404</span> an unknown id returns. There is no download control here, disabled or otherwise.</p>
            ) : (
              <p className="avail">A finished session with no artifact answers <span className="mono">409 report_unavailable</span> — distinct from the <span className="mono">409 session_not_complete</span> a running session returns, and from the <span className="mono">404</span> an unknown id returns. There is no download control here, disabled or otherwise.</p>
            )}
          </div>
        </div>
        <aside className="rail" aria-label="Run details">
          <div className="card stack" style={{ gap: "var(--space-4)" }}>
            <div className="row-between">
              <h2 className="card-title">Where it stopped</h2>
              <span className="avail-mono" id="failedHaltedAt">{haltIndex >= 0 ? `halted at stage ${haltIndex + 1}` : "halted before the first stage"}</span>
            </div>
            <Spine marks={failedMarks(run, status.status)} run={run} withArcs={false} id="spineFailed" />
          </div>
          <div className="card stack-2">
            <h2 className="card-title">What survived the halt</h2>
            <Counters counters={run.counters} absentText="not reached" pass={run.countersPass} id="failedCounters" />
            <p className="avail">The same counters the running stage keeps, frozen at the halt. A counter whose node never ran reads <span className="mono">not reached</span>: a value never measured, not a zero.</p>
          </div>
        </aside>
      </div>
    </section>
  );
}
