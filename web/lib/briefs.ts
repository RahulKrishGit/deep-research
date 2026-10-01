// The live step briefs (live-briefs spec §4.3; picks 1A, 2C, 3B): what each spine row says while it
// runs, once it is done, and when a loop reopens it. Pure — a function of RunState only, so a burst,
// a tick and a replay from event 1 paint the same brief (DESIGN.md §5.7).
import { earlierNotesText, visibleAcks, type Ack } from "./notes";
import { STAGES, countPhrase, plural, type NodeId, type PaintedMark, type ReopenLine, type RunState, type Topic, type TopicState } from "./run-state";

/* "stopped" and "off" occur only on the stopped stage's frozen spine (notes-progress-report spec §8.5). */
export type RowState = PaintedMark | "pending" | "stopped" | "off";
/* The subtitle a row shows until it is done: its static meta, or Researching's live facts line. */
export type Subtitle =
  | { kind: "text"; text: string }
  | { kind: "research"; topics: number; done: number; pages: number; findings: number };
export interface TopicLine { key: string; n: number; title: string; state: TopicState; fact: string }
export interface RowBrief {
  subtitle: Subtitle;              /* pending and active rows; green while active */
  outcome: string;                 /* done and loop rows */
  why: ReopenLine | null;          /* the first line of a row a loop reopened */
  sentence: string | null;         /* the one plain sentence of every step but Researching */
  topics: TopicLine[] | null;      /* Researching's checklist */
  titles: string[] | null;         /* Planning's final brief: the sub-topic titles */
  acks: Ack[];                     /* the active row only: the latest two notes, acknowledged (§4.7) */
  earlier: string | null;          /* the active row only: "and {n} earlier notes" past two */
}

export const STATIC_META: Readonly<Record<NodeId, string>> = Object.fromEntries(STAGES.map((s) => [s.id, s.meta])) as Record<NodeId, string>;
export const SENTENCES: Readonly<Record<Exclude<NodeId, "researcher" | "evidence_verifier">, string>> = {
  planner: "Breaking your question into sub-topics…",
  source_evaluator: "Rating sources for trustworthiness and relevance",
  report_writer: "Writing the report from verified findings only",
  report_reviewer: "Reviewing the draft on 7 dimensions",
  finalize_report: "Saving the report and evidence log",
};

export function topicFact(topic: Topic): string {
  if (topic.state === "waiting") return "not yet";
  if (topic.state === "running") return "reading";
  return countPhrase(topic.findings ?? 0, "finding", "findings");
}
/* Verifying's sentence, from researcher.research.completed.findings of the pass being verified. */
export function verifyingSentence(findings: number | null): string {
  if (findings === null) return "Checking findings against their pages";
  if (findings === 0) return "No findings to check";
  if (findings === 1) return "Checking 1 finding against its page";
  return "Checking " + findings + " findings against their pages";
}
export function subtitleText(subtitle: Subtitle): string {
  if (subtitle.kind === "text") return subtitle.text;
  const { topics, done, pages, findings } = subtitle;
  if (topics === 0) return STATIC_META.researcher;
  if (done === 0) return plural(topics, "topic", "topics") + " · researching";
  return done + " of " + topics + " " + (topics === 1 ? "topic" : "topics") + " done · "
    + countPhrase(pages, "page read", "pages read") + " · " + countPhrase(findings, "finding", "findings");
}
export function rowBrief(run: RunState, id: NodeId, state: RowState): RowBrief {
  const finished = state === "done" || state === "loop";
  const why = run.reopen[id] ?? null;
  const outcome = run.outcomes[id] ?? STATIC_META[id];
  const text: Subtitle = { kind: "text", text: STATIC_META[id] };
  // live-briefs spec §4.7: the reader's notes are acknowledged in the row that is running now.
  const noted = id === run.active ? visibleAcks(run.notes) : { acks: [], earlier: 0 };
  const notes = { acks: noted.acks, earlier: noted.earlier > 0 ? earlierNotesText(noted.earlier) : null };
  if (id === "researcher") {
    return {
      subtitle: { kind: "research", topics: run.topics.length, done: run.topics.filter((t) => t.state === "done").length,
        pages: run.pagesRead ?? 0, findings: run.findingsSoFar ?? 0 },
      outcome, why, sentence: null, titles: null, ...notes,
      topics: run.topics.map((t, i) => ({ key: t.coverageId, n: i + 1, title: t.title, state: t.state, fact: topicFact(t) })),
    };
  }
  if (id === "planner" && finished && run.plan.length > 0) {
    return { subtitle: text, outcome, why, sentence: null, topics: null, titles: run.plan.map((p) => p.title), ...notes };
  }
  const sentence = id === "evidence_verifier" ? verifyingSentence(run.passFindings) : SENTENCES[id];
  return { subtitle: text, outcome, why, sentence, topics: null, titles: null, ...notes };
}

/* notes-progress-report spec §8.5: the stopped row's subtitle — "Stopped", then the live facts the row
   had when the reader stopped it. Before Phase B only Researching has live facts; its stopped line always
   counts the topics done ("none of 3", rather than its running "3 topics · researching", which would
   contradict "Stopped", and never a bare 0). A Researching row with no topics yet, and every other row,
   reads "Stopped"; Phase B gives each step its own facts here (§6.3–§6.7). */
export function stoppedSubtitle(run: RunState, id: NodeId): string {
  const topics = run.topics.length;
  if (id !== "researcher" || topics === 0) return "Stopped";
  const done = run.topics.filter((t) => t.state === "done").length;
  return "Stopped · " + (done === 0 ? "none" : String(done)) + " of " + plural(topics, "topic", "topics") + " done · "
    + countPhrase(run.pagesRead ?? 0, "page read", "pages read") + " · " + countPhrase(run.findingsSoFar ?? 0, "finding", "findings");
}
/* A row after the stopped one: "not run", or "not run again" when the loop the run was in had re-armed
   it — it ran in an earlier pass. */
export function notRunText(run: RunState, id: NodeId): string {
  return run.rearmed[id] ? "not run again" : "not run";
}
