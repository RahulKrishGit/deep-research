// The prototype's event core (docs/design/prototype/index.html:2456-2465, :2501-2504, :2885-3054,
// :3095-3103), ported unchanged: every handler reads only `md.<key>` and the state after event k
// depends only on events 1..k, so bursts, ticks and a replay from event 1 paint the same screen.
import type { ResearchEvent, SessionStatus } from "./api";

export type NodeId = "planner" | "researcher" | "source_evaluator" | "evidence_verifier" | "report_writer" | "report_reviewer" | "finalize_report";
export type Mark = "done" | "loop" | "skipped";
export type PaintedMark = Mark | "active";
export interface Stage { id: NodeId; label: string; meta: string }

export const STAGES: readonly Stage[] = [
  { id: "planner", label: "Planning", meta: "1–10 sub-topics" },
  { id: "researcher", label: "Researching", meta: "search · scrape · read · memory" },
  { id: "source_evaluator", label: "Evaluating sources", meta: "authority · recency · relevance" },
  { id: "evidence_verifier", label: "Verifying evidence", meta: "snippet on page · context check" },
  { id: "report_writer", label: "Writing report", meta: "verified findings only · statement check" },
  { id: "report_reviewer", label: "Reviewing", meta: "7 dimensions · accept at mean 0.80" },
  { id: "finalize_report", label: "Publishing", meta: "report · evidence log · quality record" },
];
export const AGENT_ORDER: readonly NodeId[] = STAGES.map((s) => s.id);
export const ARCS: Record<"extra_pass" | "redraft", { from: NodeId; to: NodeId }> = {
  extra_pass: { from: "report_reviewer", to: "researcher" },
  redraft: { from: "report_reviewer", to: "report_writer" },
};
export const BLURB: Record<NodeId, string> = {
  planner: "Turning the question into sub-topics and evidence targets.",
  researcher: "Searching and reading; every finding keeps a verbatim snippet.",
  source_evaluator: "Scoring every source behind the findings.",
  evidence_verifier: "Checking each snippet is on its page, then each figure's context.",
  report_writer: "Drafting from verified findings; every sentence is checked against what it cites.",
  report_reviewer: "Scoring the report; accepted at a mean of 0.80 with no material defect.",
  finalize_report: "Publishing the report, the evidence log and the quality record.",
};

export interface Counters {
  subTopicsDone: number | null; subTopicsResearched: number | null; subTopicsTotal: number | null; toolCalls: number | null;
  findings: number | null; sources: number | null; verified: number | null; corrected: number | null; dropped: number | null;
  statements: number | null; refused: number | null; reviewSeen: boolean; reviewScore: number | null;
}
export interface LoopTag { kind: "extra_pass" | "redraft"; label: string; text: string }
export interface RunState {
  marks: Partial<Record<NodeId, Mark>>;   /* node id → "done" | "loop" | "skipped"; the active row is derived */
  active: NodeId | null;                  /* the "Now" row: the successor of the last graph.node.completed */
  openNode: NodeId | null;                /* the last graph.node.started with no graph.node.completed — the halting row */
  pass: number; maxPasses: number;
  loop: "off" | "flowing" | "settled"; arc: "extra_pass" | "redraft" | null;
  loopPending: boolean;                   /* a loop was routed; the reviewer's own completion is inert */
  tag: LoopTag | null;
  rearmed: Partial<Record<NodeId, true>>; rearmedFirst: NodeId | null;
  captions: Partial<Record<NodeId, string>>; blurbs: Partial<Record<NodeId, string>>;
  counters: Counters; countersPass: number;
  finalStatus: string | null;
}
export interface RunEvent { type: string; metadata: Record<string, unknown> }
// eslint-disable-next-line @typescript-eslint/no-explicit-any
type Md = Record<string, any>; // the handlers read metadata keys exactly as the prototype does
export type Handler = (run: RunState, md: Md) => void;

export function emptyCounters(): Counters {
  return { subTopicsDone: null, subTopicsResearched: null, subTopicsTotal: null, toolCalls: null,
    findings: null, sources: null, verified: null, corrected: null, dropped: null,
    statements: null, refused: null, reviewSeen: false, reviewScore: null };
}
export function newRunState(passes: number | null | undefined): RunState {
  return {
    marks: {}, active: "planner", openNode: null,
    pass: 1, maxPasses: Math.max(1, Number(passes) || 1),
    loop: "off", arc: null, loopPending: false, tag: null,
    rearmed: {}, rearmedFirst: null, captions: {}, blurbs: {},
    counters: emptyCounters(), countersPass: 1, finalStatus: null,
  };
}
function nextRow(node: NodeId): NodeId | null {
  const i = AGENT_ORDER.indexOf(node);
  return i >= 0 && i < AGENT_ORDER.length - 1 ? AGENT_ORDER[i + 1] : null;
}
/* Rows fromIndex..5 go hollow and are re-armed: their next completion reads `loop`, and the first of
   them carries ↺. Publishing goes hollow too; the rows before fromIndex keep `done`. */
function rearm(run: RunState, fromIndex: number): void {
  run.rearmed = {};
  run.rearmedFirst = AGENT_ORDER[fromIndex];
  for (let i = fromIndex; i <= 5; i++) {
    delete run.marks[AGENT_ORDER[i]];
    run.rearmed[AGENT_ORDER[i]] = true;
  }
  delete run.marks.finalize_report;
}
export function plural(n: number, one: string, many: string): string { return n + " " + (n === 1 ? one : many); }

/* Keyed by event type; each handler reads only `md` (the event's metadata). */
export const EVENT_HANDLERS: Readonly<Record<string, Handler>> = {
  "graph.session.started": (run, md) => {
    if (typeof md.max_extra_passes === "number") run.maxPasses = 1 + md.max_extra_passes;
  },
  "graph.node.started": (run, md) => {
    run.openNode = md.node;
    /* the pass number is read here and from graph.extra_pass.started only */
    if (typeof md.iteration === "number") run.pass = md.iteration + 1;
  },
  "graph.node.completed": (run, md) => {
    const node: NodeId = md.node;
    if (run.openNode === node) run.openNode = null;
    if ((node as string) === "extra_pass" || (node as string) === "writer_redraft") return;          /* hops never map to a row */
    if (node === "report_reviewer" && run.loopPending) { run.loopPending = false; return; }         /* inert after a loop decision */
    run.marks[node] = run.rearmed[node] ? "loop" : "done";
    if (node === "report_reviewer") return;                                                          /* the route decision already moved the active row */
    run.active = nextRow(node);
  },
  "graph.node.skipped": (run, md) => {
    run.marks[md.node as NodeId] = "skipped";
    if (run.active === md.node) run.active = null;
  },
  "planner.planning.completed": (run, md) => {
    run.captions.planner = plural(md.sub_topic_count, "sub-topic", "sub-topics");
  },
  "researcher.sub_topic.completed": (run) => {
    run.counters.subTopicsDone = (run.counters.subTopicsDone || 0) + 1;
  },
  "researcher.tool_call": (run) => {
    /* counted only — its `iteration` is the ReAct step index, never the pass */
    run.counters.toolCalls = (run.counters.toolCalls || 0) + 1;
  },
  "researcher.research.completed": (run, md) => {
    const c = run.counters;
    c.subTopicsResearched = md.sub_topics_researched;
    c.subTopicsTotal = md.sub_topics_researched + md.sub_topics_skipped;
    c.findings = md.findings;
  },
  "source_evaluator.evaluation.completed": (run, md) => {
    run.counters.sources = md.source_count;
  },
  "evidence_verifier.verification.completed": (run, md) => {
    const c = run.counters;
    c.verified = md.verified; c.corrected = md.verified_corrected; c.dropped = md.dropped;
  },
  "report_writer.report.written": (run, md) => {
    const c = run.counters;
    c.statements = md.statements; c.refused = md.refused;
  },
  "graph.report.reviewed": (run, md) => {
    const c = run.counters;
    c.reviewSeen = true;
    c.reviewScore = typeof md.mean_score === "number" ? md.mean_score : null;
  },
  "graph.route.decided": (run, md) => {
    run.tag = null;
    run.loopPending = false;
    if (md.destination === "extra_pass") {
      rearm(run, 1); run.active = "researcher";
      run.loopPending = true; run.arc = "extra_pass"; run.loop = "flowing";
    } else if (md.destination === "redraft") {
      rearm(run, 4); run.active = "report_writer";
      run.loopPending = true; run.arc = "redraft"; run.loop = "flowing";
    } else {
      /* finalize or end: an arc lit by an earlier loop clears here */
      run.loop = "off"; run.arc = null;
      run.active = md.destination === "finalize" ? "finalize_report" : null;
    }
  },
  "graph.extra_pass.started": (run, md) => {
    const n = (md.targets || []).length;
    if (typeof md.iteration === "number") run.pass = md.iteration + 1;
    run.loop = "settled";
    run.tag = { kind: "extra_pass", label: "extra pass", text: plural(n, "required target had no verified finding", "required targets had no verified finding") };
    run.captions.researcher = plural(n, "missing target only", "missing targets only");
    run.blurbs.researcher = "Researching the " + plural(n, "target", "targets") + " still missing a verified finding.";
    /* this-pass rows reset; whole-run and current-draft rows keep their values */
    const c = run.counters;
    c.subTopicsDone = null; c.subTopicsResearched = null; c.subTopicsTotal = null; c.findings = null;
    c.verified = null; c.corrected = null; c.dropped = null;
    run.countersPass = run.pass;
  },
  "graph.report.redraft_requested": (run, md) => {
    run.loop = "settled";
    run.tag = { kind: "redraft", label: "redraft", text: "Reviewer named " + plural(md.material_defects, "material defect", "material defects") };
    /* current-draft rows and the review score reset */
    const c = run.counters;
    c.statements = null; c.refused = null; c.reviewSeen = false; c.reviewScore = null;
  },
  "graph.session.completed": (run, md) => {
    run.finalStatus = md.status;
    run.loop = "off"; run.arc = null; run.tag = null;
    run.active = null;
  },
};
export function applyEvent(run: RunState, ev: RunEvent): void {
  const h = EVENT_HANDLERS[ev.type];
  if (h) h(run, ev.metadata || {});
}
/* The marks to paint: the recorded states plus the active row, which is derived. */
export function marksFor(run: RunState, activeId: NodeId | null): Partial<Record<NodeId, PaintedMark>> {
  const m: Partial<Record<NodeId, PaintedMark>> = {};
  (Object.keys(run.marks) as NodeId[]).forEach((k) => { m[k] = run.marks[k]; });
  if (activeId && !m[activeId]) m[activeId] = "active";
  return m;
}
export interface CounterRow { key: string; label: string; scope: string; value(c: Counters): string | { muted: string } | null }
export const COUNTER_ROWS: readonly CounterRow[] = [
  { key: "subTopics", label: "sub-topics researched", scope: "this pass",
    value: (c) => { if (c.subTopicsResearched !== null) return c.subTopicsResearched + " of " + c.subTopicsTotal; return c.subTopicsDone === null ? null : c.subTopicsDone + " this pass"; } },
  { key: "toolCalls", label: "tool calls", scope: "whole run · researcher only", value: (c) => (c.toolCalls === null ? null : String(c.toolCalls)) },
  { key: "findings", label: "findings", scope: "this pass", value: (c) => (c.findings === null ? null : String(c.findings)) },
  { key: "sources", label: "sources scored", scope: "whole run", value: (c) => (c.sources === null ? null : String(c.sources)) },
  { key: "verified", label: "verified / corrected / dropped", scope: "this pass", value: (c) => (c.verified === null ? null : c.verified + " / " + c.corrected + " / " + c.dropped) },
  { key: "statements", label: "sentences / refused", scope: "current draft", value: (c) => (c.statements === null ? null : c.statements + " / " + c.refused) },
  { key: "review", label: "review score", scope: "latest review",
    value: (c) => { if (!c.reviewSeen) return null; return c.reviewScore === null ? { muted: "not scored" } : c.reviewScore.toFixed(2); } },
];

/* The API's frame carries `event_type`; the prototype's scripts carried `type`. */
export function toRunEvent(event: ResearchEvent): RunEvent { return { type: event.event_type, metadata: event.metadata }; }
export function replayRun(events: readonly ResearchEvent[], passes: number | null | undefined): RunState {
  const run = newRunState(passes);
  for (const event of events) applyEvent(run, toRunEvent(event));
  return run;
}
/* The failed stage's marks: the halting row is the open node; Publishing is skipped by the client
   rule status == "failed" (never has_report); an API-level failure has no graph.session.completed,
   so the session's own status is the fallback (index.html:3745-3746). */
export function failedMarks(run: RunState, sessionStatus: SessionStatus): Partial<Record<NodeId, PaintedMark>> {
  const marks = marksFor(run, run.openNode);
  if ((run.finalStatus || sessionStatus) === "failed") marks.finalize_report = "skipped";
  return marks;
}
