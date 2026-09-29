// The live step briefs (live-briefs spec §4.3; picks 1A, 2C, 3B): what each spine row says while it
// runs, once it is done, and when a loop reopens it. Pure — a function of RunState only, so a burst,
// a tick and a replay from event 1 paint the same brief (DESIGN.md §5.7).
import { STAGES, countPhrase, plural, type NodeId, type PaintedMark, type ReopenLine, type RunState, type Topic, type TopicState } from "./run-state";

export type RowState = PaintedMark | "pending";
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
  if (id === "researcher") {
    return {
      subtitle: { kind: "research", topics: run.topics.length, done: run.topics.filter((t) => t.state === "done").length,
        pages: run.pagesRead ?? 0, findings: run.findingsSoFar ?? 0 },
      outcome, why, sentence: null, titles: null,
      topics: run.topics.map((t, i) => ({ key: t.coverageId, n: i + 1, title: t.title, state: t.state, fact: topicFact(t) })),
    };
  }
  if (id === "planner" && finished && run.plan.length > 0) {
    return { subtitle: text, outcome, why, sentence: null, topics: null, titles: run.plan.map((p) => p.title) };
  }
  const sentence = id === "evidence_verifier" ? verifyingSentence(run.passFindings) : SENTENCES[id];
  return { subtitle: text, outcome, why, sentence, topics: null, titles: null };
}
