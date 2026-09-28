// What only the submitting tab knows: that a session was just submitted (the Submitted beat) and
// the settings it was submitted with (the strip). Memory first, sessionStorage under the
// prototype's own prefix so a reload in the same tab keeps them.
export interface SubmittedSettings { model: string; thinking: "enabled" | "disabled"; extraPasses: number; outputDir: string }
export interface Submission { submittedAt: number; settings: SubmittedSettings }
export const SUBMITTED_BEAT_MS = 2200;

const PREFIX = "dr.console.submission.";
const memory = new Map<string, Submission>();
const storage = () => (typeof window === "undefined" ? null : window.sessionStorage);

export function recordSubmission(sessionId: string, settings: SubmittedSettings): Submission {
  const submission = { submittedAt: Date.now(), settings };
  memory.set(sessionId, submission);
  storage()?.setItem(PREFIX + sessionId, JSON.stringify(submission));
  return submission;
}
export function readSubmission(sessionId: string): Submission | null {
  const held = memory.get(sessionId);
  if (held) return held;
  const raw = storage()?.getItem(PREFIX + sessionId);
  if (!raw) return null;
  try { const parsed = JSON.parse(raw) as Submission; memory.set(sessionId, parsed); return parsed; } catch { return null; }
}
/* Milliseconds of the Submitted beat still to show for this session in this tab; 0 when none. */
export function submittedBeatRemaining(sessionId: string, now = Date.now()): number {
  const submission = readSubmission(sessionId);
  return submission ? Math.max(0, SUBMITTED_BEAT_MS - (now - submission.submittedAt)) : 0;
}
