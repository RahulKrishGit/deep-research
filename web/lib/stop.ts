// Stop in the web app: the confirmation's copy,
// the stopped stage's sentences, and the label of the step a stop records. Pure — a function of the
// status response and the stream's RunState only.
import { stepLabel } from "./run-state";

export const STOP_LABEL = "Stop";
export const STOP_TITLE = "Stop this research?";
export const STOP_BODY = "It stops right away and nothing more is spent. What's done so far stays here, but no report is written.";
export const STOP_KEEP = "Keep going";
export const STOP_CONFIRM = "Stop research";
export const STOP_TOO_LATE = "Too late to stop — the research is finishing.";
export const STOP_CLOSE = "Close";
export const STOP_FAILED = "Couldn't stop — try again";
export const STOPPED_EYEBROW = "Stopped by you";
export const STOPPED_KEPT = "No report was written. The plan and what research found so far are kept below until the service restarts.";
export const ASK_AGAIN = "Ask again";
/* A POST /research from "Ask again" that failed. */
export const ASK_AGAIN_FAILED = "Couldn't ask again — try again";

/* The label of the step a stop records: a row's STAGES label, "the questions" for the
   one-time check, null for none or a step the page does not know. */
export function stoppedStepLabel(step: string | null | undefined): string | null {
  return step === "check" ? "the questions" : stepLabel(step);
}
/* Whole minutes the run had run: "less than a minute in", "1 minute in", "{n} minutes in". */
export function minutesIn(seconds: number): string {
  if (seconds < 60) return "less than a minute in";
  const minutes = Math.floor(seconds / 60);
  return minutes === 1 ? "1 minute in" : minutes + " minutes in";
}
/* HH:MM in the reader's own time zone, 24-hour; null for a missing or unreadable time. */
export function localClock(iso: string | null | undefined): string | null {
  if (!iso) return null;
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return null;
  return String(d.getHours()).padStart(2, "0") + ":" + String(d.getMinutes()).padStart(2, "0");
}
/* Whole seconds from a run's start to its end; null when either is missing or the end comes first. */
export function secondsBetween(startedAt: string | null | undefined, finishedAt: string | null | undefined): number | null {
  if (!startedAt || !finishedAt) return null;
  const ms = new Date(finishedAt).getTime() - new Date(startedAt).getTime();
  return Number.isFinite(ms) && ms >= 0 ? Math.floor(ms / 1000) : null;
}
/* The stopped note's first line: when, and how far in — or, for a stop during the one-time
   check, that the run had not started. A part the page cannot read is left out, never invented. */
export function stoppedLine(step: string | null, at: string | null | undefined, seconds: number | null): string {
  const clock = localClock(at);
  const said = "You stopped this research" + (clock === null ? "" : " at " + clock);
  if (step === "check") return said + ", before it started.";
  return seconds === null ? said + "." : said + ", " + minutesIn(seconds) + ".";
}
