"use client";
import type { EvidenceResponse, ResearchSessionResponse } from "@/lib/api";
import { fmtScore, meterClass, passFact, statusNote, toSessionView } from "@/lib/format";

const ratio = (a: number, b: number) => (Number.isFinite(a) && b > 0 ? a / b : null);
function Meter({ id, label, value, absent, gate }: { id: string; label: string; value: number | null; absent: string; gate?: boolean }) {
  const cls = value === null ? null : meterClass(value);
  return (
    <div className="bar-row"><span style={{ color: "var(--fg)" }}>{label}</span>
      <span className={`bar${gate ? " bar-gate" : ""}`}><span id={`${id}Fill`} className={cls ?? undefined} style={{ width: value === null ? "0%" : `${Math.round(Math.max(0, Math.min(1, value)) * 100)}%` }}></span></span>
      <span className={`n${value === null ? " avail" : ""}`} id={`${id}N`}>{value === null ? absent : fmtScore(value)}</span>
    </div>
  );
}

export function ReportRail({ status, evidence }: { status: ResearchSessionResponse; evidence: EvidenceResponse | null }) {
  const view = toSessionView(status);
  const ec = status.evidence_counts;
  const cited = ec ? ratio(ec.cited_assessed_sources, ec.assessed_sources) : null;
  const cov = status.coverage;
  const questions = new Map((evidence?.not_found ?? []).map((t) => [t.target_id, t.question]));
  const notFound = cov ? cov.not_found_target_ids.map((id) => (questions.has(id) ? `${id} — ${questions.get(id)}` : id)) : [];
  const recoverable = status.errors.filter((e) => e.recoverable).length;
  return (
    <aside className="rail" aria-label="Report details">
      <div className="card stack-2">
        <h2 className="card-title">Review</h2>
        <div className="bars">
          <Meter id="repReview" label="Review score" value={status.semantic_review_score} absent="not scored" gate />
          <Meter id="repCited" label="Scored sources cited" value={cited} absent="not measured" />
        </div>
        <p className="avail" id="repReviewStatus">{statusNote(view)}</p>
      </div>
      <div className="card stack-2">
        <h2 className="card-title">Coverage</h2>
        <dl className="kv">
          <dt>required targets answered</dt><dd id="repCovAnswered" className={cov ? undefined : "avail"}>{cov ? `${cov.answered_targets} of ${cov.required_targets}` : "not measured"}</dd>
          <dt>not found</dt><dd id="repCovNotFound" className={!cov || !notFound.length ? "avail" : undefined}>{!cov ? "not measured" : notFound.length ? notFound.join(" · ") : "none"}</dd>
        </dl>
      </div>
      <div className="card stack-2">
        <h2 className="card-title">Evidence</h2>
        <dl className="kv" id="repEvidenceCounts" hidden={!ec}>
          {ec ? [["findings", ec.findings], ["verified", ec.verified_findings], ["corrected", ec.corrected_findings], ["quoted", ec.quoted_findings], ["dropped", ec.dropped_findings],
            ["context unchecked", ec.context_unchecked_findings], ["cited", ec.cited_findings], ["reads", `${ec.network_reads} network · ${ec.cache_reads} cache`], ["unique works", ec.unique_works], ["publishers", ec.publishers]]
            .map(([k, v]) => [<dt key={`${k}-k`}>{k}</dt>, <dd key={`${k}-v`}>{String(v)}</dd>]) : null}
        </dl>
        <p className="avail" id="repEvidenceNone" hidden={!!ec}>not measured</p>
      </div>
      <div className="card stack-2">
        <h2 className="card-title">Session facts</h2>
        <dl className="kv">
          <dt>status</dt><dd id="repFactStatus">{status.status}</dd>
          <dt>pass</dt><dd id="repFactPass">{passFact(status.iteration)}</dd>
          <dt>started_at</dt><dd id="repFactStarted">{status.started_at}</dd>
          <dt>finished_at</dt><dd id="repFactFinished" className={status.finished_at ? undefined : "avail"}>{status.finished_at ?? "not recorded"}</dd>
          <dt>duration_seconds</dt><dd id="repFactDuration" className={status.duration_seconds === null ? "avail" : undefined}>{status.duration_seconds === null ? "not recorded" : String(status.duration_seconds)}</dd>
          <dt>report_path</dt><dd id="repFactPath" className={status.report_path ? undefined : "avail"}>{status.report_path ?? "Not published"}</dd>
          <dt>evidence_path</dt><dd id="repFactEvidence" className={status.evidence_path ? undefined : "avail"}>{status.evidence_path ?? "Not published"}</dd>
          <dt>quality_path</dt><dd id="repFactQuality" className={status.quality_path ? undefined : "avail"}>{status.quality_path ?? "Not published"}</dd>
          <dt>errors</dt><dd id="repFactErrors">{status.errors.length}</dd>
        </dl>
      </div>
      <div className="card stack-2">
        <h2 className="card-title">Cost and usage</h2>
        <dl className="kv">
          <dt>tool calls</dt><dd className="avail">Not recorded</dd>
          <dt>input tokens</dt><dd className="avail">Not recorded</dd>
          <dt>output tokens</dt><dd className="avail">Not recorded</dd>
        </dl>
        <p className="avail">The response carries no token or tool-call totals. The running stage's tool-call counter is a stream-derived, researcher-only figure and is not copied here. Nothing is rendered as <span className="mono">0</span>.</p>
      </div>
      <div className="card stack-2">
        <h2 className="card-title">Errors</h2>
        <details className="disc">
          <summary><span id="repErrSummary">{recoverable} recoverable · {status.errors.length - recoverable} non-recoverable</span></summary>
          <div className="disc-body stack-2" id="repErrList">
            {status.errors.length === 0 ? <span className="avail">No errors recorded.</span> : status.errors.map((e, i) => (
              <div key={i}><span className="avail-mono">{e.error_type}</span>{e.message ? <span className="sm" style={{ display: "block", marginTop: "var(--space-1)" }}>{e.message}</span> : null}</div>
            ))}
          </div>
        </details>
      </div>
    </aside>
  );
}
