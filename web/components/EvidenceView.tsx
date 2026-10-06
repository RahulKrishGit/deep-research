"use client";
import { useEffect, useState, type KeyboardEvent } from "react";
import type { EvidenceFinding, EvidenceNotFound, EvidenceRefused, EvidenceResponse } from "@/lib/api";
import { PILL_TEXT, VERIFICATION_TEXT, fmtScore, meterClass } from "@/lib/format";

export const EV_FILTERS = [
  { id: "all", label: "All" }, { id: "verified", label: "Verified" }, { id: "verified_corrected", label: "Corrected" }, { id: "quoted", label: "Quoted" },
  { id: "dropped", label: "Dropped" }, { id: "not_found", label: "Not found" }, { id: "refused", label: "Refused" },
] as const;
export type EvidenceRow =
  | { kind: "finding"; status: string; id: string; item: EvidenceFinding }
  | { kind: "not_found"; status: "not_found"; id: string; item: EvidenceNotFound }
  | { kind: "refused"; status: "refused"; id: string; item: EvidenceRefused };

/* Findings sort by label — F-labels in numeric order first, then
   X-labels (the ids api/evidence.py gives a finding the report never registered) in numeric order
   — ahead of the not-found and refused rows, which keep the response's own order.
   The response itself is not re-sorted (api/evidence.py keeps recording order, matching the evidence log's
   own "### {label}" heading order) — this is a presentation-only sort in the view. */
const labelSortKey = (label: string): [number, number] => [label.startsWith("X") ? 1 : 0, parseInt(label.slice(1), 10) || 0];
export function evidenceRows(data: EvidenceResponse): EvidenceRow[] {
  const findings = [...data.findings].sort((a, b) => {
    const [ka, na] = labelSortKey(a.label);
    const [kb, nb] = labelSortKey(b.label);
    return ka !== kb ? ka - kb : na - nb;
  });
  return [
    ...findings.map((f): EvidenceRow => ({ kind: "finding", status: f.status ?? "not_checked", id: f.label, item: f })),
    ...data.not_found.map((t): EvidenceRow => ({ kind: "not_found", status: "not_found", id: t.target_id, item: t })),
    ...data.refused.map((r, i): EvidenceRow => ({ kind: "refused", status: "refused", id: "R" + String(i + 1).padStart(2, "0"), item: r })),
  ];
}
const firstSentence = (s: string | null) => { const m = /^(.*?[.!?])(\s|$)/.exec(s ?? ""); return m ? m[1] : s ?? ""; };
const Pill = ({ status }: { status: string }) => <span className="pill" data-status={status}>{PILL_TEXT[status] ?? status}</span>;
function MeterRow({ label, value }: { label: string; value: number | null }) {
  const cls = value === null ? null : meterClass(value);
  return (
    <div className="bar-row"><span>{label}</span><span className="bar"><span className={cls ?? undefined} style={{ width: typeof value === "number" ? `${Math.round(value * 100)}%` : "0%" }}></span></span>
      <span className={`n${fmtScore(value) === null ? " avail" : ""}`}>{fmtScore(value) ?? "not scored"}</span></div>
  );
}

export function EvidenceView({ evidence }: { evidence: EvidenceResponse }) {
  const rows = evidenceRows(evidence);
  const [filter, setFilter] = useState<string>("all");
  const [selected, setSelected] = useState(0);
  useEffect(() => { setFilter("all"); setSelected(0); }, [evidence]);
  const visible = filter === "all" ? rows : rows.filter((r) => r.status === filter);
  const current = visible[Math.min(selected, Math.max(0, visible.length - 1))] ?? null;
  const selectLabel = (label: string) => { setFilter("all"); const idx = rows.findIndex((r) => r.id === label); setSelected(idx < 0 ? 0 : idx); };
  const onKey = (e: KeyboardEvent) => {
    if (e.key === "ArrowDown") { e.preventDefault(); setSelected((s) => Math.min(visible.length - 1, s + 1)); }
    if (e.key === "ArrowUp") { e.preventDefault(); setSelected((s) => Math.max(0, s - 1)); }
  };
  return (
    <div className="with-rail evidence-view">
      <div className="stack" style={{ gap: "var(--space-4)" }}>
        <div className="chip-filters" id="evChips" role="group" aria-label="Filter evidence by status">
          {EV_FILTERS.map((f) => {
            const count = f.id === "all" ? rows.length : rows.filter((r) => r.status === f.id).length;
            return <button key={f.id} type="button" className="chip-filter" data-filter={f.id} aria-pressed={filter === f.id} onClick={() => { setFilter(f.id); setSelected(0); }}>{f.label} <span className="n">{count}</span></button>;
          })}
        </div>
        <ol className="ev-list" id="evList" role="listbox" aria-label="Evidence" tabIndex={0} onKeyDown={onKey}>
          {visible.map((r, i) => (
            <li key={`${r.kind}-${r.id}`} className="ev-row" role="option" data-kind={r.kind} data-status={r.status} data-id={r.id} aria-selected={i === selected} onClick={() => setSelected(i)}>
              <Pill status={r.status} /><span className="lbl">{r.id}</span>
              {r.kind === "finding" ? <span className="txt">{firstSentence(r.item.snippet)}</span> : r.kind === "not_found" ? <span className="txt">{r.item.question}</span> : <span className="txt">{r.item.text}</span>}
              <span className="tags">
                {r.kind === "finding" ? <>{r.item.target_ids.map((t) => <span key={t} className="tag">{t}</span>)}{r.item.context_unchecked ? <span className="tag soft">context unchecked</span> : null}</>
                  : r.kind === "not_found" ? <span className="tag soft">{r.item.searched ? `${r.item.queries.length} queries · ${r.item.pages_read.length} pages read` : "not searched in this run"}</span>
                  : <span className="tag soft">{r.item.finding_labels.length ? `cited ${r.item.finding_labels.join(", ")}` : "cited nothing"}</span>}
              </span>
            </li>
          ))}
        </ol>
        <p className="avail" id="evEmpty" hidden={visible.length > 0}>Nothing to show under this filter.</p>
      </div>
      <aside className="rail ev-detail card stack-2" id="evDetail" aria-label="Evidence detail" aria-live="polite">
        {current === null ? <p className="avail">Nothing to show under this filter.</p> : current.kind === "finding" ? <FindingDetail f={current.item} /> : current.kind === "not_found" ? <NotFoundDetail t={current.item} /> : <RefusedDetail id={current.id} r={current.item} onSelectLabel={selectLabel} />}
      </aside>
    </div>
  );
}

function FindingDetail({ f }: { f: EvidenceFinding }) {
  const verb = f.status === "dropped" ? `dropped (${f.dropped_reason})` : VERIFICATION_TEXT[f.status ?? "not_checked"];
  const kept = f.figures.find((x) => x.kept && x.organisation);
  const context = !f.figures.length ? "not run (no figures)" : f.context_unchecked || !kept ? "context unchecked" : [kept.organisation, kept.kind, kept.release].filter(Boolean).join(" · ");
  return (
    <>
      <h2 className="ev-title">{f.label} — {f.source.title || "untitled source"}</h2>
      <p className="ev-status">{verb}{f.context_unchecked ? "; context unchecked" : ""}</p>
      <div className="stack-2"><span className="cap">Snippet</span><blockquote>{f.snippet}</blockquote>{f.passage ? <><span className="cap">Passage</span><blockquote>{f.passage}</blockquote></> : null}</div>
      <p className="sm">Source: <a className="tlink" href={f.source.url} target="_blank" rel="noopener">{f.source.title || f.source.url}</a></p>
      <p className="sm">Context check: {context}</p>
      <div className="bars">
        <MeterRow label="authority" value={f.source.authority_score} /><MeterRow label="recency" value={f.source.recency_score} />
        <MeterRow label="relevance" value={f.source.relevance_score} /><MeterRow label="overall" value={f.source.overall_score} />
      </div>
      {f.figures.length ? <><span className="cap">Figures</span><ul className="ev-figures">{f.figures.map((x, i) => <li key={i}>{x.kept ? `${x.value}: kept · period ${x.period || "not stated"} · scope ${x.scope || "not stated"}${x.corrected ? " · corrected" : ""}` : `${x.value}: dropped (${x.dropped_reason}) ${x.reason || ""}`}</li>)}</ul></> : null}
    </>
  );
}
function NotFoundDetail({ t }: { t: EvidenceNotFound }) {
  return (
    <>
      <h2 className="ev-title">{t.target_id} — not found</h2>
      <p className="ev-status">{t.question}</p>
      {!t.searched ? <p className="avail">not searched in this run</p> : <>
        <span className="cap">Searched</span><ul className="ev-queries">{t.queries.map((q) => <li key={q}>{q}</li>)}</ul>
        <span className="cap">Pages read</span><ul className="ev-pages">{t.pages_read.map((u) => <li key={u}><a className="tlink" href={u} target="_blank" rel="noopener">{u}</a></li>)}</ul>
      </>}
    </>
  );
}
function RefusedDetail({ id, r, onSelectLabel }: { id: string; r: EvidenceRefused; onSelectLabel(label: string): void }) {
  return (
    <>
      <h2 className="ev-title">{id} — refused sentence</h2>
      <blockquote>{r.text}</blockquote>
      <p className="ev-cited sm">{r.finding_labels.length ? "cited " : "cited nothing"}{r.finding_labels.map((l) => <button key={l} type="button" onClick={() => onSelectLabel(l)}>{l}</button>)}</p>
      <dl className="kv"><dt>reason</dt><dd className="plain">{r.reason}</dd><dt>where</dt><dd className="plain">{r.where}</dd></dl>
    </>
  );
}
