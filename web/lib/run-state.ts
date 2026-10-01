// The prototype's event core (docs/design/prototype/index.html:2456-2465, :2501-2504, :2885-3054,
// :3095-3103), ported unchanged: every handler reads only `md.<key>` and the state after event k
// depends only on events 1..k, so bursts, ticks and a replay from event 1 paint the same screen.
// live-briefs spec §4.3 (2026-09-28) extends it with the step briefs' state — the topic checklist,
// the outcome lines and the reopen lines — on the same rule; `open` is the one field the reader,
// not the stream, writes.
import type { ResearchEvent, SessionStatus } from "./api";
import { fmtScore } from "./format";
import { noteRedraftLine, notePassLine } from "./notes";

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
/* live-briefs spec §4.7: a note pass returns to Researching like the extra pass, drawn with the
   redraft's stroke (--meta), because a note pass is not a warning. */
export const ARCS: Record<"extra_pass" | "redraft" | "note_pass", { from: NodeId; to: NodeId }> = {
  extra_pass: { from: "report_reviewer", to: "researcher" },
  redraft: { from: "report_reviewer", to: "report_writer" },
  note_pass: { from: "report_reviewer", to: "researcher" },
};
export interface Counters {
  subTopicsDone: number | null; subTopicsResearched: number | null; subTopicsTotal: number | null; toolCalls: number | null;
  findings: number | null; sources: number | null; verified: number | null; corrected: number | null; dropped: number | null;
  statements: number | null; refused: number | null; reviewSeen: boolean; reviewScore: number | null;
}
/* The step briefs' state (live-briefs spec §4.3). A topic is one planned sub-topic in this pass's
   Researching checklist; "waiting" until its researcher.sub_topic.started, "running" until its
   researcher.sub_topic.completed. */
export type TopicState = "waiting" | "running" | "done";
export interface Topic { coverageId: string; title: string; state: TopicState; findings: number | null }
export interface PlannedTopic { coverageId: string; title: string }
/* The first line of a row a loop reopened: why it reopened (the old loop tag's content). */
export interface ReopenLine { kind: "extra_pass" | "redraft" | "note_pass" | "note_redraft"; text: string }
/* One reader note (live-briefs spec §4.6-§4.7): as received, then as the run read it. `where` is the
   row that was active when session.note.interpreted arrived — the step the acknowledgement names. */
export interface NoteState {
  id: string; text: string; interpreted: boolean; restatement: string | null;
  replaces: string | null; fallback: boolean; where: NodeId | null;
  /* notes-progress-report spec §5.7: the run's reading of the note's kinds ([] until it is read), and
     whether its own research thread has started — a researcher.sub_topic.started naming it. */
  kinds: string[]; threadStarted: boolean;
}
/* The one-time check (live-briefs spec §4.4-§4.5): its questions and deadline from
   session.clarification.requested, then the answers the run starts with from .answered. */
export interface ClarifyQuestion { id: string; dimension: string; text: string; short: string; options: string[]; bestGuess: string }
export interface ClarifyAnswer { questionId: string; value: string; source: "chosen" | "typed" | "best_guess" }
export interface ClarifyState { questions: ClarifyQuestion[]; deadlineAt: string; answered: { answers: ClarifyAnswer[]; reason: string } | null }
/* notes-progress-report spec §8.2, §8.4: a stopped session's last event — the step it was on (a row's
   node id, or "check"), when (ISO) and how long it had run. A value the event does not carry is null,
   never invented. */
export interface StoppedRun { step: string | null; at: string | null; elapsedSeconds: number | null }
export interface RunState {
  marks: Partial<Record<NodeId, Mark>>;   /* node id → "done" | "loop" | "skipped"; the active row is derived */
  active: NodeId | null;                  /* the "Now" row: the successor of the last graph.node.completed */
  openNode: NodeId | null;                /* the last graph.node.started with no graph.node.completed — the halting row */
  pass: number;
  loop: "off" | "flowing" | "settled"; arc: "extra_pass" | "redraft" | "note_pass" | null;
  loopPending: boolean;                   /* a loop was routed; the reviewer's own completion is inert */
  rearmed: Partial<Record<NodeId, true>>; rearmedFirst: NodeId | null;
  captions: Partial<Record<NodeId, string>>;
  counters: Counters; countersPass: number;
  finalStatus: string | null;
  plan: PlannedTopic[];                   /* planner.planning.completed.metadata.sub_topics, in plan order */
  topics: Topic[];                        /* this pass's Researching checklist */
  pagesRead: number | null;               /* Σ successful_reads over this pass's completed topics */
  findingsSoFar: number | null;           /* Σ findings_retained over completed topics, then researcher.research.completed.findings */
  passFindings: number | null;            /* the latest researcher.research.completed.findings (Verifying's brief) */
  reopen: Partial<Record<NodeId, ReopenLine>>;
  outcomes: Partial<Record<NodeId, string>>;  /* each row's outcome line once it is done */
  open: Set<NodeId>;                      /* done rows the reader reopened — reader state, not derived from events */
  clarify: ClarifyState | null;           /* the one-time check; null while the stream has told none */
  notes: NoteState[];                     /* the reader's notes, in receipt order */
  stopped: StoppedRun | null;             /* session.stopped: the reader stopped the run; null otherwise */
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
export function newRunState(): RunState {
  return {
    marks: {}, active: "planner", openNode: null,
    pass: 1,
    loop: "off", arc: null, loopPending: false,
    rearmed: {}, rearmedFirst: null, captions: {},
    counters: emptyCounters(), countersPass: 1, finalStatus: null,
    plan: [], topics: [], pagesRead: null, findingsSoFar: null, passFindings: null,
    reopen: {}, outcomes: {}, open: new Set(), clarify: null, notes: [], stopped: null,
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
    run.open.delete(AGENT_ORDER[i]);
  }
  delete run.marks.finalize_report;
}
export function plural(n: number, one: string, many: string): string { return n + " " + (n === 1 ? one : many); }
/* The label of the row a node runs on, for the chip's "Running · {step}" (live-briefs spec §4.2).
   The two hops lead back into a row, so they read as that row; anything else is not a row. */
const HOP_ROW: Readonly<Record<string, NodeId>> = { extra_pass: "researcher", note_pass: "researcher", writer_redraft: "report_writer" };
export function stepLabel(node: string | null | undefined): string | null {
  if (!node) return null;
  const id = HOP_ROW[node] ?? node;
  return STAGES.find((s) => s.id === id)?.label ?? null;
}
/* The node the chip names while the stream is open: the active row; once graph.session.completed
   has arrived (and until /status turns terminal), the row the run ended on — Publishing, or the
   node that halted. Between Publishing's own completion and graph.session.completed no row is active
   and none is open, so the chip keeps naming Publishing rather than fall back to a stale /status. */
export function chipStep(run: RunState): string | null {
  if (run.active) return run.active;
  if (run.finalStatus === null) return run.openNode ?? (run.marks.finalize_report ? "finalize_report" : null);
  if (run.finalStatus === "failed") return run.openNode;
  return "finalize_report";
}
/* A measured count in words: 0 reads "no …", never a bare 0 (live-briefs spec AC5). */
export function countPhrase(n: number, one: string, many: string): string { return n === 0 ? "no " + many : plural(n, one, many); }
const count = (v: unknown): number => (typeof v === "number" && Number.isFinite(v) ? v : 0);
const topicId = (md: Md): string => (typeof md.coverage_id === "string" && md.coverage_id ? md.coverage_id : "index-" + String(md.index));
function topicFor(run: RunState, md: Md): Topic {
  const id = topicId(md);
  const known = run.topics.find((t) => t.coverageId === id);
  if (known) return known;
  const title = run.plan.find((p) => p.coverageId === id)?.title ?? (typeof md.sub_topic === "string" ? md.sub_topic : id);
  const topic: Topic = { coverageId: id, title, state: "waiting", findings: null };
  run.topics.push(topic);
  return topic;
}
/* Reviewing's outcome line, read at the route decision (the review's score arrived just before it). */
function reviewOutcome(run: RunState, md: Md): string {
  if (md.reason === "note_pass_requested") return "Sent back to research your note";
  if (md.reason === "note_redraft_requested") return "Sent back to the writer for your note";
  if (md.destination === "extra_pass") {
    const k = Array.isArray(md.missing_required_target_ids) ? md.missing_required_target_ids.length : 0;
    return k > 0 ? "Sent back to fill " + plural(k, "gap", "gaps") : "Sent back for more research";
  }
  const score = fmtScore(run.counters.reviewScore);
  if (md.reason === "report_accepted") return score === null ? "Accepted" : "Accepted · " + score;
  return score === null ? "Review unavailable" : "Not accepted · " + score;
}
export function toggleOpen(run: RunState, id: NodeId): void {
  if (run.open.has(id)) run.open.delete(id); else run.open.add(id);
}

/* The check's wire shapes (api/clarify.py): an entry that does not fit is dropped, never invented. */
const isText = (v: unknown): v is string => typeof v === "string" && v.length > 0;
function isClarifyQuestion(q: unknown): q is { id: string; dimension: string; text: string; short: string; options: string[]; best_guess: string } {
  const m = q as Md | null;
  return !!m && isText(m.id) && isText(m.dimension) && isText(m.text) && isText(m.short)
    && Array.isArray(m.options) && m.options.every(isText) && isText(m.best_guess);
}
function isClarifyAnswer(a: unknown): a is { question_id: string; value: string; source: ClarifyAnswer["source"] } {
  const m = a as Md | null;
  return !!m && isText(m.question_id) && typeof m.value === "string" && ["chosen", "typed", "best_guess"].includes(m.source);
}

/* Keyed by event type; each handler reads only `md` (the event's metadata). */
export const EVENT_HANDLERS: Readonly<Record<string, Handler>> = {
  "graph.node.started": (run, md) => {
    run.openNode = md.node;
    /* the pass number is read here and from graph.extra_pass.started only */
    if (typeof md.iteration === "number") run.pass = md.iteration + 1;
  },
  "graph.node.completed": (run, md) => {
    const node: NodeId = md.node;
    if (run.openNode === node) run.openNode = null;
    if ((node as string) === "extra_pass" || (node as string) === "note_pass" || (node as string) === "writer_redraft") return; /* hops never map to a row */
    if (node === "report_reviewer" && run.loopPending) { run.loopPending = false; return; }         /* inert after a loop decision */
    run.marks[node] = run.rearmed[node] ? "loop" : "done";
    if (node === "finalize_report") run.outcomes.finalize_report = "Published";
    if (node === "report_reviewer") return;                                                          /* the route decision already moved the active row */
    run.active = nextRow(node);
  },
  "graph.node.skipped": (run, md) => {
    run.marks[md.node as NodeId] = "skipped";
    if (run.active === md.node) run.active = null;
  },
  "planner.planning.completed": (run, md) => {
    run.captions.planner = plural(md.sub_topic_count, "sub-topic", "sub-topics");
    run.outcomes.planner = run.captions.planner;
    const listed: unknown[] = Array.isArray(md.sub_topics) ? md.sub_topics : [];
    run.plan = listed.filter((t): t is { coverage_id: string; title: string } =>
      !!t && typeof (t as Md).coverage_id === "string" && typeof (t as Md).title === "string")
      .map((t) => ({ coverageId: t.coverage_id, title: t.title }));
    run.topics = run.plan.map((p) => ({ coverageId: p.coverageId, title: p.title, state: "waiting", findings: null }));
    run.pagesRead = null; run.findingsSoFar = null;
  },
  "researcher.sub_topic.started": (run, md) => {
    topicFor(run, md).state = "running";
    /* notes-progress-report spec §5.7: a reader note's own thread names its note */
    const note = isText(md.note_id) ? run.notes.find((n) => n.id === md.note_id) : undefined;
    if (note) note.threadStarted = true;
  },
  "researcher.sub_topic.completed": (run, md) => {
    run.counters.subTopicsDone = (run.counters.subTopicsDone || 0) + 1;
    const topic = topicFor(run, md);
    const kept = count(md.findings_retained);
    topic.state = "done"; topic.findings = kept;
    run.pagesRead = (run.pagesRead ?? 0) + count(md.successful_reads);
    run.findingsSoFar = (run.findingsSoFar ?? 0) + kept;
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
    run.findingsSoFar = count(md.findings);
    run.passFindings = count(md.findings);
    run.outcomes.researcher = [countPhrase(count(md.sub_topics_researched), "topic", "topics"),
      countPhrase(run.pagesRead ?? 0, "page read", "pages read"), countPhrase(count(md.findings), "finding", "findings")].join(" · ");
  },
  "source_evaluator.evaluation.completed": (run, md) => {
    run.counters.sources = md.source_count;
    run.outcomes.source_evaluator = plural(count(md.source_count), "source rated", "sources rated");
  },
  "evidence_verifier.verification.completed": (run, md) => {
    const c = run.counters;
    c.verified = md.verified; c.corrected = md.verified_corrected; c.dropped = md.dropped;
    run.outcomes.evidence_verifier = count(md.verified) + " verified · " + count(md.verified_corrected) + " corrected · " + count(md.dropped) + " dropped";
  },
  "report_writer.report.written": (run, md) => {
    const c = run.counters;
    c.statements = md.statements; c.refused = md.refused;
    run.outcomes.report_writer = "Report drafted · " + plural(count(md.statements), "sentence", "sentences") + " · " + plural(count(md.citations), "citation", "citations");
  },
  "graph.report.reviewed": (run, md) => {
    const c = run.counters;
    c.reviewSeen = true;
    c.reviewScore = typeof md.mean_score === "number" ? md.mean_score : null;
  },
  "graph.route.decided": (run, md) => {
    run.loopPending = false;
    run.outcomes.report_reviewer = reviewOutcome(run, md);
    if (md.destination === "extra_pass") {
      rearm(run, 1); run.active = "researcher";
      run.loopPending = true; run.arc = "extra_pass"; run.loop = "flowing";
      /* the pass about to run starts its own checklist; the writer's next draft is not a redraft */
      run.topics = []; run.pagesRead = null; run.findingsSoFar = null; run.passFindings = null;
      /* spec §5.7: "now" means the note's own thread runs in this researcher run; an earlier run's thread no longer counts */
      for (const n of run.notes) n.threadStarted = false;
      delete run.reopen.report_writer;
    } else if (md.destination === "note_pass") {
      /* live-briefs spec §4.6: the note pass re-runs Researching onward, as the extra pass does */
      rearm(run, 1); run.active = "researcher";
      run.loopPending = true; run.arc = "note_pass"; run.loop = "flowing";
      run.topics = []; run.pagesRead = null; run.findingsSoFar = null; run.passFindings = null;
      /* spec §5.7: a note pass's own started event sets it again */
      for (const n of run.notes) n.threadStarted = false;
      delete run.reopen.report_writer;
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
    run.captions.researcher = plural(n, "missing target only", "missing targets only");
    run.reopen.researcher = { kind: "extra_pass", text: "Going back to research " + plural(n, "gap", "gaps") + " the review found" };
    /* this-pass rows reset; whole-run and current-draft rows keep their values */
    const c = run.counters;
    c.subTopicsDone = null; c.subTopicsResearched = null; c.subTopicsTotal = null; c.findings = null;
    c.verified = null; c.corrected = null; c.dropped = null;
    run.countersPass = run.pass;
  },
  "graph.report.redraft_requested": (run, md) => {
    run.loop = "settled";
    run.reopen.report_writer = { kind: "redraft", text: "Rewriting to fix " + plural(count(md.material_defects), "issue", "issues") + " the review found" };
    /* current-draft rows and the review score reset */
    const c = run.counters;
    c.statements = null; c.refused = null; c.reviewSeen = false; c.reviewScore = null;
  },
  /* live-briefs spec §4.7: the note pass researches only the notes' own sub-topics — "Your note: …" —
     and its first line says which notes it is for. Like an extra pass it starts its own this-pass
     rows and drops the earlier pass's researching caption; whole-run and current-draft rows keep
     their values. */
  "graph.note_pass.started": (run, md) => {
    const ids: string[] = Array.isArray(md.note_ids) ? md.note_ids.filter(isText) : [];
    run.loop = "settled";
    delete run.captions.researcher;
    const c = run.counters;
    c.subTopicsDone = null; c.subTopicsResearched = null; c.subTopicsTotal = null; c.findings = null;
    c.verified = null; c.corrected = null; c.dropped = null;
    run.reopen.researcher = { kind: "note_pass", text: notePassLine(ids, run.notes) };
    run.outcomes.report_reviewer = ids.length === 1 ? "Sent back to research your note" : "Sent back to research " + ids.length + " of your notes";
    run.topics = ids.map((id) => {
      const note = run.notes.find((n) => n.id === id);
      return { coverageId: "note-" + id, title: "Your note: " + (note ? note.restatement ?? note.text : id), state: "waiting", findings: null };
    });
    run.pagesRead = null; run.findingsSoFar = null;
  },
  "graph.note_redraft.requested": (run, md) => {
    const ids: string[] = Array.isArray(md.note_ids) ? md.note_ids.filter(isText) : [];
    run.loop = "settled";
    run.reopen.report_writer = { kind: "note_redraft", text: noteRedraftLine(ids, run.notes) };
    run.outcomes.report_reviewer = ids.length === 1 ? "Sent back to the writer for your note" : "Sent back to the writer for " + ids.length + " of your notes";
    const c = run.counters;
    c.statements = null; c.refused = null; c.reviewSeen = false; c.reviewScore = null;
  },
  "graph.session.completed": (run, md) => {
    run.finalStatus = md.status;
    run.loop = "off"; run.arc = null;
    run.active = null;
  },
  /* live-briefs spec §4.4: the session asks before the graph starts; neither event moves a row. */
  "session.clarification.requested": (run, md) => {
    const listed: unknown[] = Array.isArray(md.questions) ? md.questions : [];
    run.clarify = {
      questions: listed.filter(isClarifyQuestion).map((q) => ({ id: q.id, dimension: q.dimension, text: q.text, short: q.short, options: [...q.options], bestGuess: q.best_guess })),
      deadlineAt: typeof md.deadline_at === "string" ? md.deadline_at : "",
      answered: null,
    };
  },
  "session.clarification.answered": (run, md) => {
    const listed: unknown[] = Array.isArray(md.answers) ? md.answers : [];
    const answers = listed.filter(isClarifyAnswer).map((a) => ({ questionId: a.question_id, value: a.value, source: a.source }));
    run.clarify = { questions: run.clarify?.questions ?? [], deadlineAt: run.clarify?.deadlineAt ?? "", answered: { answers, reason: typeof md.reason === "string" ? md.reason : "" } };
  },
  /* live-briefs spec §4.6-§4.7: a note is acknowledged as received at once, then as the run read
     it; neither event moves a row. A replayed note is never counted twice. */
  "session.note.received": (run, md) => {
    if (!isText(md.note_id) || run.notes.some((n) => n.id === md.note_id)) return;
    run.notes.push({ id: md.note_id, text: typeof md.text === "string" ? md.text : "", interpreted: false, restatement: null, replaces: null, fallback: false, where: null, kinds: [], threadStarted: false });
  },
  "session.note.interpreted": (run, md) => {
    if (!isText(md.note_id)) return;
    let note = run.notes.find((n) => n.id === md.note_id);
    if (!note) { note = { id: md.note_id, text: "", interpreted: false, restatement: null, replaces: null, fallback: false, where: null, kinds: [], threadStarted: false }; run.notes.push(note); }
    note.interpreted = true;
    note.restatement = isText(md.restatement) ? md.restatement : null;
    note.kinds = Array.isArray(md.kinds) ? md.kinds.filter(isText) : [];
    note.replaces = isText(md.replaces) ? md.replaces : null;
    note.fallback = md.fallback === true;
    note.where = run.active;
  },
  /* notes-progress-report spec §8.4: the reader stopped the run. No row is active and no loop is lit;
     every mark stays as recorded, so the stopped stage can freeze the pipeline where it was. */
  "session.stopped": (run, md) => {
    run.stopped = {
      step: isText(md.step) ? md.step : null,
      at: isText(md.stopped_at) ? md.stopped_at : null,
      elapsedSeconds: typeof md.elapsed_seconds === "number" && Number.isFinite(md.elapsed_seconds) ? md.elapsed_seconds : null,
    };
    run.active = null; run.loop = "off"; run.arc = null;
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
export function replayRun(events: readonly ResearchEvent[]): RunState {
  const run = newRunState();
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
