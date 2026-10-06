// What only the submitting tab knows: that a session was just submitted (the Submitted beat) and
// the settings it was submitted with (the strip). Memory first, sessionStorage under the
// prototype's own prefix so a reload in the same tab keeps them.
// askWhenUnclear: the one-time check's setting. Only the request reads it; a
// Submission stored before it existed has none, and nothing downstream of the POST needs it.
export interface SubmittedSettings { model: string; thinking: "enabled" | "disabled"; outputDir: string; askWhenUnclear: boolean }
// beatMs: the submitted-beat budget computed at submit time (lib/handoff.ts's
// submittedBeatBudgetMs — DESIGN.md §5.6, "the submitted beat"). Optional so a Submission
// read back from an older sessionStorage entry (no field) still falls back to SUBMITTED_BEAT_MS.
export interface Submission { submittedAt: number; settings: SubmittedSettings; beatMs?: number }
// The with-motion budget (320 clear + 900 lift + 200 dissolve + 750 hold — DESIGN.md §5.6)
// used as recordSubmission's default and as submittedBeatRemaining's fallback for a Submission
// with no beatMs of its own.
export const SUBMITTED_BEAT_MS = 2170;

const PREFIX = "dr.console.submission.";
const memory = new Map<string, Submission>();
const storage = () => (typeof window === "undefined" ? null : window.sessionStorage);

export function recordSubmission(sessionId: string, settings: SubmittedSettings, beatMs: number = SUBMITTED_BEAT_MS): Submission {
  const submission = { submittedAt: Date.now(), settings, beatMs };
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
  return submission ? Math.max(0, (submission.beatMs ?? SUBMITTED_BEAT_MS) - (now - submission.submittedAt)) : 0;
}
