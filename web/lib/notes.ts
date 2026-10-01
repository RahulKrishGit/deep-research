// Reader notes in the web app (live-briefs spec §4.7; D8, D9, D11a; pick 6A,
// docs/design/running-stage-picks/Hitl3.dc.html column A): the note line's copy, each note's
// acknowledgement, the first lines of the loops a note buys, and the report's outcome words.
// Pure — a function of RunState or of the status response only, so a burst and a replay from
// event 1 paint the same (DESIGN.md §5.7).
import type { ReaderNoteOutcome } from "./api";
import type { NodeId, NoteState } from "./run-state";

/* D11a: a run takes at most ten notes. The page never says so: the line only disables. */
export const NOTE_LIMIT = 10;
export const NOTE_MAX_CHARS = 500;
export const NOTE_PLACEHOLDER = "Add a note — something to focus on, leave out or change";
export const NOTE_FIELD_LABEL = "Add a note for this research";
export const NOTE_SEND_LABEL = "Add note";
export const NOTES_CLOSED = "Notes are closed — the report is being published";
/* Any failure but the two 409s (a 5xx, a network error): the text stays, and this caption says so
   until the next edit (this plan's words; the spec names only the two 409s). */
export const NOTE_SEND_FAILED = "Couldn't send — try again";

/* Where a note takes effect, by the step that was active when the run read it (§4.7 table).
   Publishing has none: notes are closed by then. */
export const WHERE: Readonly<Partial<Record<NodeId, string>>> = {
  planner: ", shaping the plan",
  researcher: ", from each topic's next search",
  source_evaluator: ", in how sources are rated and in the report",
  evidence_verifier: ", in the report",
  report_writer: ", in this draft",
  report_reviewer: "; the review will check it",
};

/* notes-progress-report spec §5.7 (D1-D4): a research note — its kinds include new_angle, a mixed note
   included — is researched as its own topic, so its acknowledgement says when, by the step that was
   running when the run read it. "Now" while Researching is the active row and the note's own thread
   has started — a planning-time topic, a research-time thread or a note pass — and only then: a note
   read after the researcher's window closed gets no thread in that run. */
export const RESEARCH_WHERE: Readonly<Partial<Record<NodeId, string>>> = {
  planner: ", as its own topic",
  researcher: ", as its own topic",
  source_evaluator: ", researched as its own topic after this draft is reviewed",
  evidence_verifier: ", researched as its own topic after this draft is reviewed",
  report_writer: ", researched as its own topic after this draft is reviewed",
  report_reviewer: ", researched as its own topic next",
};
export const RESEARCH_NOW = ", researching it as its own topic now";
export const isResearchNote = (note: NoteState): boolean => note.kinds.includes("new_angle");
function whereFor(note: NoteState, active: NodeId | null): string {
  if (!note.where) return "";
  if (!isResearchNote(note)) return WHERE[note.where] ?? "";
  if (active === "researcher" && note.threadStarted) return RESEARCH_NOW;
  return RESEARCH_WHERE[note.where] ?? "";
}

/* One acknowledgement line: `lead`, then the run's reading in `said` (the pick's .said), then `rest`.
   `active` is the run's active row (RunState.active); a research note's "now" needs it. */
export interface Ack { key: string; lead: string; said: string | null; rest: string }

export function ackFor(note: NoteState, notes: readonly NoteState[], active: NodeId | null = null): Ack {
  if (!note.interpreted) return { key: note.id, lead: "Reading your note…", said: null, rest: "" };
  if (note.fallback) return { key: note.id, lead: "Got it — passed on as you wrote it", said: null, rest: "" };
  const earlier = note.replaces ? notes.find((n) => n.id === note.replaces) : undefined;
  const replacing = earlier ? ", replacing your earlier note about " + (earlier.restatement ?? earlier.text) : "";
  return { key: note.id, lead: "Got it — ", said: note.restatement ?? note.text, rest: whereFor(note, active) + replacing };
}

/* §4.7: with more than two notes, the brief shows the latest two (oldest first) and how many
   earlier notes it leaves out. */
export function visibleAcks(notes: readonly NoteState[], active: NodeId | null = null): { acks: Ack[]; earlier: number } {
  const shown = notes.slice(-2);
  return { acks: shown.map((note) => ackFor(note, notes, active)), earlier: notes.length - shown.length };
}
export function earlierNotesText(n: number): string {
  return "and " + n + (n === 1 ? " earlier note" : " earlier notes");
}

/* The words a loop's first line uses for the notes it serves: each one's reading, in receipt order. */
export function noteSubjects(ids: readonly string[], notes: readonly NoteState[]): string {
  return ids.map((id) => {
    const note = notes.find((n) => n.id === id);
    return note ? note.restatement ?? note.text : id;
  }).join("; ");
}
export function notePassLine(ids: readonly string[], notes: readonly NoteState[]): string {
  return (ids.length === 1 ? "Researching your note: " : "Researching your notes: ") + noteSubjects(ids, notes);
}
export function noteRedraftLine(ids: readonly string[], notes: readonly NoteState[]): string {
  return (ids.length === 1 ? "Rewriting for your note: " : "Rewriting for your notes: ") + noteSubjects(ids, notes);
}

/* The outcome words, one per ReaderNoteOutcome (§4.7; "not addressed in the report", "not checked" and
   "replaced by a later note" are this plan's words for the three outcomes the spec's table leaves
   unnamed). Kept and pinned by its own test; no component reads it, since the report's notes moved
   into its bottom line (notes-progress-report spec §7.2). A note the report still ignores is never
   "covered". A session that has ended reads "not_checked" for a note nothing judged, and "pending" —
   "not checked yet" — occurs only while a run is going (spec §4 item 2). */
export const OUTCOME_TEXT: Readonly<Record<ReaderNoteOutcome, string>> = {
  covered: "covered",
  not_found: "couldn't find evidence",
  not_addressed: "not addressed in the report",
  pending: "not checked yet",
  not_checked: "not checked",
  replaced: "replaced by a later note",
};

/* How many more notes the run takes: the last /status's count, lowered by every note the stream has
   seen since (a note is counted once it is received, whether or not it has been read yet). */
export function notesLeft(statusRemaining: number | undefined, received: number): number {
  return Math.max(0, Math.min(statusRemaining ?? NOTE_LIMIT, NOTE_LIMIT - received));
}
