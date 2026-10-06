// The prototype's display helpers (docs/design/prototype/index.html), with types added;
// toSessionView adapts the API's flat fields to the prototype's session shape so statusNote
// keeps its body.
import type { CoverageProgress, ResearchSessionResponse, SessionStatus } from "./api";

export interface SessionView {
  status: SessionStatus;
  iteration: number;
  /* The running row's label ("Running · {step}"), or the label of the step a
     stopped session was stopped at ("Stopped by you · at {step}");
     null when not known. */
  step: string | null;
  review: { status: string | null; score: number | null } | null;
  coverage: CoverageProgress | null;
}

export function toSessionView(s: ResearchSessionResponse, step: string | null = null): SessionView {
  const review =
    s.semantic_review_status === null && s.semantic_review_score === null
      ? null
      : { status: s.semantic_review_status, score: s.semantic_review_score };
  return { status: s.status, iteration: s.iteration, step, review, coverage: s.coverage };
}

/* Label and dot per API status (api/models.py, SessionStatus). The second clause is built by
   statusNote(). A session the reader stopped sits on a neutral dot: stopping is neither a failure
   nor a warning. */
export const STATUS: Record<SessionStatus, { label: string; dot: "dot-live" | "dot-ok" | "dot-warn" | "dot-danger" | "dot-neutral" }> = {
  running: { label: "Running", dot: "dot-live" },
  needs_input: { label: "Waiting for you", dot: "dot-warn" },
  completed: { label: "Completed", dot: "dot-ok" },
  max_iterations: { label: "Partially completed", dot: "dot-warn" },
  incomplete: { label: "Partially completed", dot: "dot-warn" },
  failed: { label: "Failed", dot: "dot-danger" },
  stopped: { label: "Stopped by you", dot: "dot-neutral" },
};
/* A session still in progress: running, or waiting for the reader's answers to the one-time check
   (needs_input is not terminal). */
export function isLive(status: SessionStatus): boolean {
  return status === "running" || status === "needs_input";
}

/* The prototype's setQuestionFit(): a short question sits in the middle of the frame; the
   .ask-locked.q-center CSS rule (globals.css) does the actual centring. */
export const Q_CENTER_MAX = 80;
export function qFitClass(q: string): string {
  return (q || "").length <= Q_CENTER_MAX ? " q-center" : "";
}
/* The report's pass fact in plain words: the extra research passes the
   run took (`iteration`, zero-based), then the passes it took for the reader's notes
   (`note_passes`, 0 when there were none). */
const times = (n: number) => (n === 1 ? "once" : n === 2 ? "twice" : n + " times");
export function passFact(iteration: number, notePasses = 0): string {
  const research = iteration > 0 ? "Went back " + times(iteration) + " to fill gaps" : "One research round";
  return notePasses > 0 ? research + " · went back " + times(notePasses) + " for your notes" : research;
}
/* Scores print with two decimals; null is "no score", never 0. */
export function fmtScore(v: unknown): string | null {
  return typeof v === "number" && Number.isFinite(v) ? v.toFixed(2) : null;
}
/* coverage.not_found_target_ids → " · 1 target not found" / " · n targets not found" / "" */
export function notFoundClause(s: SessionView): string {
  const ids = (s && s.coverage && s.coverage.not_found_target_ids) || [];
  if (!ids.length) return "";
  return " · " + ids.length + (ids.length === 1 ? " target not found" : " targets not found");
}
/* The chip's second clause, one rule per API status. */
export function statusNote(s: SessionView): string {
  const score = fmtScore(s.review && s.review.score);
  switch (s.status) {
    case "completed": return "review accepted" + (score === null ? "" : " · " + score) + notFoundClause(s);
    case "max_iterations": return "extra passes used" + notFoundClause(s);
    case "incomplete": return s.review && s.review.status === "scored" && score !== null ? "not accepted · " + score : "review unavailable";
    case "needs_input": return "a few quick questions";
    case "failed": return "halted";
    case "stopped": return s.step === null ? "step not recorded" : "at " + s.step;
    default: return s.step ?? "starting";
  }
}
export function fmtSeconds(s: number | null): string | null {
  if (typeof s !== "number" || !Number.isFinite(s) || s < 0) return null;
  const t = Math.round(s);
  return Math.floor(t / 60) + "m " + String(t % 60).padStart(2, "0") + "s";
}
export function fmtClock(iso: string | null): string | null {
  if (!iso) return null;
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return null;
  return String(d.getUTCHours()).padStart(2, "0") + ":" + String(d.getUTCMinutes()).padStart(2, "0") + "Z";
}
export function fmtElapsed(sec: number): string {
  return String(Math.floor(sec / 60)).padStart(2, "0") + ":" + String(sec % 60).padStart(2, "0");
}
/* Meter colour is a judgement about the number (DESIGN.md §3.6): v > 0.8 is green, so exactly 0.80
   paints yellow on purpose while acceptance is ≥ 0.80. Do not "fix" either side. */
export function meterClass(v: number): "ok" | "warn" | "danger" | null {
  if (Number.isNaN(v)) return null;
  return v > 0.8 ? "ok" : v >= 0.4 ? "warn" : "danger";
}
/* Halting types (the graph's failure state) in plain words; the two api.research.* types are the
   API layer's own failures, which never emit graph.session.completed. */
export const HALT_HEADLINES: Readonly<Record<string, string>> = {
  graph_planning_failed: "Planning failed",
  graph_provider_configuration_error: "Model provider misconfigured",
  graph_agent_configuration_error: "Agent misconfigured",
  graph_invalid_agent_state: "Invalid agent state",
  graph_invalid_route: "Invalid route",
  graph_request_attempt_limit_exceeded: "Request attempt limit reached",
  "api.research.configuration_error": "Service configuration error",
  "api.research.failed": "Research run failed",
};
export const PILL_TEXT: Readonly<Record<string, string>> = {
  verified: "verified", verified_corrected: "corrected", quoted: "quoted", dropped: "dropped",
  not_checked: "not checked", not_found: "not found", refused: "refused",
};
/* The evidence log's verbs (agents/report.py). */
export const VERIFICATION_TEXT: Readonly<Record<string, string>> = {
  verified: "verified",
  verified_corrected: "verified with corrections",
  quoted: "quoted (snippet found on the page; not checked for context)",
  not_checked: "not checked",
};
