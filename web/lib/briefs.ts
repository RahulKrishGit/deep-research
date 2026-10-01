// The live step briefs: what each spine row says while it runs, once it is done, when a loop reopens
// it and when the reader stopped the run on it. live-briefs spec §4.3 (picks 1A, 2C, 3B) built the
// frame; notes-progress-report spec §6 (2026-09-30) gives every step its own body: Planning's slots
// (Main.dc.html B), Evaluating's bar and split (Evaluating.dc.html A), Verifying's and Writing's
// tickers (Verifying.dc.html C, Writing.dc.html E) and Reviewing's checks (Reviewing.dc.html A
// revised). Pure — a function of RunState and the clock only, so a burst, a tick and a replay from
// event 1 paint the same brief (DESIGN.md §5.7).
import { fmtSeconds } from "./format";
import { earlierNotesText, visibleAcks, type Ack } from "./notes";
import {
  STAGES, countPhrase, notAcceptedLine, plural, thingsToFix,
  type NodeId, type NoteState, type PaintedMark, type ReopenLine, type ReviewHalf, type ReviewNoteResult,
  type RunState, type SlotState, type Topic, type TopicState, type VerifierSample, type WriterSample, type WritingState,
} from "./run-state";

/* "stopped" and "off" occur only on the stopped stage's frozen spine (notes-progress-report spec §8.5). */
export type RowState = PaintedMark | "pending" | "stopped" | "off";
/* The subtitle a row shows until it is done: a line of text, or Researching's live facts line. A
   count the run has not measured is null: its phrase is left out, never printed as 0 (D19). */
export type Subtitle =
  | { kind: "text"; text: string }
  | { kind: "research"; topics: number; done: number; pages: number | null; findings: number | null };
export interface TopicLine { key: string; n: number; title: string; state: TopicState; fact: string }
export interface RowBrief {
  subtitle: Subtitle;              /* pending, active, stopped and off rows; green while active */
  outcome: string;                 /* done and loop rows */
  why: ReopenLine | null;          /* the first line of a row a loop reopened */
  body: BriefBody;                 /* the step's own lines (§6.3-§6.7) */
  acks: Ack[];                     /* the active row only: the latest two notes, acknowledged */
  earlier: string | null;          /* the active row only: "and {n} earlier notes" past two */
}

export const STATIC_META: Readonly<Record<NodeId, string>> = Object.fromEntries(STAGES.map((s) => [s.id, s.meta])) as Record<NodeId, string>;
/* §6.9: Publishing keeps its one plain sentence; every other step has its own body. */
export const SENTENCES: Readonly<Pick<Record<NodeId, string>, "finalize_report">> = {
  finalize_report: "Saving the report and evidence log",
};

export function topicFact(topic: Topic): string {
  if (topic.state === "waiting") return "not yet";
  if (topic.state === "running") return "reading";
  return countPhrase(topic.findings ?? 0, "finding", "findings");
}
export function subtitleText(subtitle: Subtitle): string {
  if (subtitle.kind === "text") return subtitle.text;
  const { topics, done, pages, findings } = subtitle;
  if (topics === 0) return STATIC_META.researcher;
  if (done === 0) return plural(topics, "topic", "topics") + " · researching";
  return done + " of " + topics + " " + (topics === 1 ? "topic" : "topics") + " done" + measuredCounts(pages, findings);
}
/* " · {pages} · {findings}", each only when measured: a measured 0 reads in words ("no findings"), an
   unmeasured count is left out (D19: unknown values never read as 0). */
function measuredCounts(pages: number | null, findings: number | null): string {
  return (pages === null ? "" : " · " + countPhrase(pages, "page read", "pages read"))
    + (findings === null ? "" : " · " + countPhrase(findings, "finding", "findings"));
}
function subtitleFor(run: RunState, id: NodeId, state: RowState, nowMs: number): Subtitle {
  if (state === "stopped") return { kind: "text", text: stoppedSubtitle(run, id) };
  if (state === "off") return { kind: "text", text: notRunText(run, id) };
  if (id === "researcher") return researchSubtitle(run);
  if (state === "active") {
    const live = liveSubtitle(run, id, nowMs);
    if (live !== null) return { kind: "text", text: live };
  }
  /* §6.7 (review 2, M-4): Reviewing's static meta names the notes while the run holds one. */
  if (id === "report_reviewer" && listedNotes(run.notes).length > 0) return { kind: "text", text: STATIC_META.report_reviewer + " · your notes" };
  return { kind: "text", text: STATIC_META[id] };
}
function outcomeFor(run: RunState, id: NodeId): string {
  const outcome = run.outcomes[id];
  if (outcome === undefined) return STATIC_META[id];
  const seconds = run.durations[id];
  /* §6.3, §6.7: Planning's and Reviewing's outcomes end with the row's duration. */
  return (id === "planner" || id === "report_reviewer") && typeof seconds === "number" ? outcome + " · " + fmtSeconds(seconds) : outcome;
}
function bodyFor(run: RunState, id: NodeId, state: RowState): BriefBody {
  const stopped = state === "stopped";
  switch (id) {
    case "planner": return { kind: "planning", status: planningStatus(run), slots: planningSlots(run, stopped) };
    case "researcher":
      return { kind: "research", topics: run.topics.map((t, i) => ({ key: t.coverageId, n: i + 1, title: t.title, state: t.state, fact: topicFact(t) })) };
    case "source_evaluator": return evaluatingBody(run);
    case "evidence_verifier": return verifyingBody(run);
    case "report_writer": return writingBody(run, state === "done" || state === "loop");
    case "report_reviewer": return reviewingBody(run, stopped);
    default: return { kind: "sentence", text: SENTENCES.finalize_report };
  }
}
/* `nowMs` is the page's one-second clock (RunningPipeline), so the elapsed times tick; a burst, a tick
   and a replay given the same clock paint the same brief. */
export function rowBrief(run: RunState, id: NodeId, state: RowState, nowMs: number = Date.now()): RowBrief {
  // live-briefs spec §4.7: the reader's notes are acknowledged in the row that is running now.
  const noted = id === run.active ? visibleAcks(run.notes, run.active) : { acks: [], earlier: 0 };
  return {
    subtitle: subtitleFor(run, id, state, nowMs),
    outcome: outcomeFor(run, id),
    why: run.reopen[id] ?? null,
    body: bodyFor(run, id, state),
    acks: noted.acks,
    earlier: noted.earlier > 0 ? earlierNotesText(noted.earlier) : null,
  };
}

/* notes-progress-report spec §8.5: the stopped row's subtitle — "Stopped", then the live facts the row
   had when the reader stopped it, frozen at the stop (§6.3-§6.7). Researching's always counts the topics
   done ("none of 3", rather than its running "3 topics · researching", which would contradict
   "Stopped", and never a bare 0), then the pages and findings the run measured and only those
   ("Stopped · none of 3 topics done", D19); a row with no live facts reads "Stopped". */
export function stoppedSubtitle(run: RunState, id: NodeId): string {
  if (id === "researcher") {
    const topics = run.topics.length;
    if (topics === 0) return "Stopped";
    const done = run.topics.filter((t) => t.state === "done").length;
    return "Stopped · " + (done === 0 ? "none" : String(done)) + " of " + plural(topics, "topic", "topics") + " done"
      + measuredCounts(run.pagesRead, run.findingsSoFar);
  }
  const at = run.stopped?.at ? Date.parse(run.stopped.at) : NaN;
  const live = liveSubtitle(run, id, Number.isNaN(at) ? null : at);
  return live === null ? "Stopped" : "Stopped · " + live;
}
/* A row after the stopped one: "not run", or "not run again" when the loop the run was in had re-armed
   it — it ran in an earlier pass. */
export function notRunText(run: RunState, id: NodeId): string {
  return run.rearmed[id] ? "not run again" : "not run";
}

/* ═══ notes-progress-report spec §6.3-§6.7: each step's own brief ═══ */
/* What a checklist row's `.mk` draws (its data-topic): ○ waiting, ● running, ✓ done, amber ✗ fail;
   "stopped" is a slot that was running when the reader stopped the run — the ring, as Researching's. */
export type CheckMark = "waiting" | "running" | "done" | "fail" | "stopped";
/* One Planning slot: a skeleton bar until its title is known (`title` null); `gone` for a surplus
   skeleton the plan's titles leave over, which fades out; `rise` for a row that arrives later. */
export interface SlotLine { key: string; n: number; title: string | null; width: string | null; mark: CheckMark; fact: string; gone: boolean; rise: boolean }
/* One Reviewing row: a criterion or a note, `before` while the review runs, `fact` once it landed. */
export interface CheckLine { key: string; text: string; mark: CheckMark; before: string; fact: string; landed: boolean }
/* One ticker sample: the finding or the drafted sentence, its verdict's words, where it came from. */
export interface TickerLine { key: string; text: string; quoted: boolean; verdict: string; kept: boolean; where: string | null }
/* A cross-fading status line: every text in one grid cell, the `on`th one shown. */
export interface StatusStack { texts: string[]; on: number }
export interface EvaluatingStats { rated: number | null; toRate: number; strong: number | null; fair: number | null; weak: number | null }
export interface VerifyTally { checked: number; total: number; verified: number; corrected: number; dropped: number }
export interface WritingTally { checked: number; drafted: number; backed: number; removed: number; unchecked: number; partsReturned: number; partsTotal: number }
export type BriefBody =
  | { kind: "planning"; status: StatusStack; slots: SlotLine[] }
  | { kind: "research"; topics: TopicLine[] }
  | { kind: "evaluating"; lead: string; bar: number | null; stats: EvaluatingStats | null }
  | { kind: "verifying"; empty: string | null; samples: TickerLine[]; bar: number; tally: VerifyTally | null }
  | { kind: "writing"; placeholder: string; samples: TickerLine[]; bar: number; tally: WritingTally | null }
  | { kind: "reviewing"; status: StatusStack; waiting: boolean; criteria: CheckLine[]; notes: CheckLine[] }
  | { kind: "sentence"; text: string };
export const VERIFY_PLACEHOLDER = "The first findings are being checked…";
export const WRITING_PLACEHOLDER = "The first section is being drafted…";
/* §6.6: once a section has returned, until the first sentence comes back checked. */
export const WRITING_CHECKING_PLACEHOLDER = "The first sentences are being checked…";
/* §6.6, E7: every drafted sentence is settled and none could be checked (each Statement Check failed). */
export const WRITING_NONE_CHECKED_PLACEHOLDER = "None of the drafted sentences could be checked";
/* §6.6, P3-4: every part has returned and not one sentence was drafted (each point refused), so none is or will be checked. */
export const WRITING_NO_SENTENCES_PLACEHOLDER = "No sentences were drafted to check";
/* §6.6, owner decision O2 (2026-10-01): while the bottom line runs and no section drafted a sentence this pass (a
   note pass whose own part was fully refused, or a pass with no part to draft), what the ticker waits on is the
   bottom line, as the subtitle says ("writing the bottom line"). */
export const WRITING_BOTTOM_LINE_PLACEHOLDER = "Writing the bottom line…";
/* §6.3: four skeleton slots before the plan's titles; the count is a placeholder, not a claim. */
export const SKELETON_WIDTHS: readonly string[] = ["78%", "64%", "72%", "52%"];
/* §6.7, D23, D35: the five criteria a review can mark not met, in DIMENSION_GUIDANCE order. */
export const REVIEW_CRITERIA: readonly { dimension: string; label: string }[] = [
  { dimension: "completeness", label: "Covers your whole question" },
  { dimension: "evidence_quality", label: "Rests on strong evidence" },
  { dimension: "attribution", label: "Every claim is credited correctly" },
  { dimension: "uncertainty", label: "Honest about what is uncertain" },
  { dimension: "readability", label: "Easy to read" },
];
export const ISSUE_WORDS: Readonly<Record<string, string>> = {
  coverage: "a part of your question has no answer",
  mechanism: "a step in the explanation is missing",
  missing_support: "a sentence says more than its sources",
  acquisition: "a needed source could not be read",
  source_quality: "a claim rests on a weak source",
  freshness: "a figure is out of date",
  identity: "a source is credited to the wrong publisher",
  contradiction: "sources disagree and the draft does not say so",
  semantic_duplicate: "the same point is made twice",
  presentation: "a sentence is hard to follow",
};
export const NOTE_RESULT_WORDS: Readonly<Record<string, string>> = {
  covered: "covered", not_found: "not found", to_research: "researched next",
  honoured: "honoured", ignored_with_evidence: "not followed", no_evidence: "no evidence found", not_judged: "not checked",
};
export const SOURCE_WORDS: Readonly<Record<string, string>> = {
  original_report: "an original report", independent_research: "independent research",
  derivative: "a round-up of other sources", company_statement: "the business's own words",
};
export const DROP_WORDS: Readonly<Record<string, string>> = {
  read_not_found: "the page could not be read again",
  snippet_not_on_page: "the page does not say this",
  evidence_not_on_page: "the page does not show this figure",
  correction_not_on_page: "the page does not back its date or scope",
  context_rejected: "the page's context does not support it",
  context_unavailable: "its figures could not be checked",
};
const CORRECTION_WORDS: Readonly<Record<string, (value: string | null) => string>> = {
  period: (v) => "the page dates it " + (v ?? ""),
  period_cleared: () => "the page states no period for it",
  scope: (v) => "the page says it covers " + (v ?? ""),
  subject: (v) => "the page says it is about " + (v ?? ""),
  kind: (v) => (v === "forecast" ? "the page states it as a forecast" : "the page states it as an actual"),
  figure: () => "one of its figures was not on the page",
};
const SLOT_MARK: Readonly<Record<SlotState, CheckMark>> = {
  skeleton: "waiting", drafted: "waiting", checking: "running", being_fixed: "running",
  passed: "done", fixed: "done", flagged: "fail", not_checked: "waiting",
};
const SLOT_FACT: Readonly<Record<SlotState, string>> = {
  skeleton: "", drafted: "", checking: "checking", being_fixed: "being fixed",
  passed: "", fixed: "fixed", flagged: "still flagged", not_checked: "not checked",
};

const capitalise = (s: string): string => (s ? s[0].toUpperCase() + s.slice(1) : s);
/* `Xm SSs` from an ISO start to `toMs`; null with no start or no clock. */
export function elapsedText(fromIso: string | undefined, toMs: number | null): string | null {
  if (!fromIso || toMs === null) return null;
  const from = Date.parse(fromIso);
  return Number.isNaN(from) ? null : fmtSeconds(Math.max(0, (toMs - from) / 1000));
}
const researchSubtitle = (run: RunState): Subtitle => ({
  kind: "research", topics: run.topics.length, done: run.topics.filter((t) => t.state === "done").length,
  pages: run.pagesRead, findings: run.findingsSoFar,
});

/* §6.3: the status line. */
export function planningStatus(run: RunState): StatusStack {
  const p = run.planning;
  const answered = (run.clarify?.answered?.answers.length ?? 0) > 0;
  const fixing = p.slots.filter((s) => s.state === "being_fixed").length;
  return {
    texts: [
      answered ? "Reading your question and your answers…" : "Reading your question…",
      "Drafting a plan for your question…",
      p.round === 2 ? "Checking the fixed plan…" : "Checking the plan covers everything you asked…",
      fixing > 0 ? "Fixing " + plural(fixing, "topic", "topics") + " the check flagged…" : "Fixing what the check found…",
      "Plan ready · research starts now",
    ],
    on: ({ reading: 0, drafting: 1, checking: 2, fixing: 3, ready: 4 } as const)[p.step],
  };
}
/* §6.3: the slots — skeletons before the titles, then one per topic, then one per research note. A slot
   still running when the reader stopped the run reads "stopped", with the ring (§8.5). */
export function planningSlots(run: RunState, stopped: boolean): SlotLine[] {
  const p = run.planning;
  const lines: SlotLine[] = [];
  if (p.slots.length === 0) {
    SKELETON_WIDTHS.forEach((width, i) => lines.push({
      key: "slot-" + (i + 1), n: i + 1, title: null, width, mark: "waiting",
      fact: p.step === "drafting" && !stopped ? "drafting" : "", gone: false, rise: false,
    }));
  } else {
    p.slots.forEach((slot, i) => {
      const halted = stopped && SLOT_MARK[slot.state] === "running";
      lines.push({
        key: "slot-" + (i + 1), n: i + 1, title: slot.title, width: SKELETON_WIDTHS[i] ?? null,
        mark: halted ? "stopped" : SLOT_MARK[slot.state], fact: halted ? "stopped" : SLOT_FACT[slot.state],
        gone: false, rise: i >= SKELETON_WIDTHS.length,
      });
    });
    for (let i = p.slots.length; i < SKELETON_WIDTHS.length; i++) {
      lines.push({ key: "slot-" + (i + 1), n: i + 1, title: null, width: SKELETON_WIDTHS[i], mark: "waiting", fact: "", gone: true, rise: false });
    }
  }
  const shown = lines.filter((line) => !line.gone).length;
  p.noteSlots.forEach((slot, i) => lines.push({
    key: "note-" + slot.noteId, n: shown + i + 1, title: slot.title, width: null,
    mark: slot.state === "planned" ? "done" : "waiting", fact: slot.state === "planned" ? "from your note" : "joins the plan",
    gone: false, rise: true,
  }));
  return lines;
}

/* §6.4 */
export function evaluatingBody(run: RunState): Extract<BriefBody, { kind: "evaluating" }> {
  const e = run.evaluating;
  if (e === null) {
    return { kind: "evaluating", lead: "Rating sources for trustworthiness and relevance", bar: 0,
      stats: { rated: null, toRate: 0, strong: null, fair: null, weak: null } };
  }
  if (e.toRate === 0) return { kind: "evaluating", lead: "No new sources to rate", bar: null, stats: null };
  const lead = e.reused > 0
    ? "Rating " + plural(e.toRate, "new source", "new sources") + " · " + e.reused + " already rated"
    : "Rating " + plural(e.toRate, "source", "sources") + " for trustworthiness and relevance";
  const landed = e.batchesDone > 0;
  return {
    kind: "evaluating", lead, bar: Math.min(1, (e.rated + e.unrated) / e.toRate),
    stats: { rated: landed ? e.rated : null, toRate: e.toRate, strong: landed ? e.strong : null, fair: landed ? e.fair : null, weak: landed ? e.weak : null },
  };
}

/* §6.5 */
export function verifierVerdict(sample: VerifierSample): string {
  if (sample.verdict === "verified") return "verified";
  if (sample.verdict === "quoted") return "quoted as written";
  if (sample.verdict === "verified_corrected") {
    const words = sample.correction ? CORRECTION_WORDS[sample.correction.field] : undefined;
    return words ? "corrected — " + words(sample.correction!.value) : "corrected";
  }
  const reason = sample.dropReason ? DROP_WORDS[sample.dropReason] : undefined;
  return reason ? "dropped — " + reason : "dropped";
}
export function sourceWords(sample: VerifierSample): string | null {
  return (sample.role ? SOURCE_WORDS[sample.role] : undefined) ?? sample.host;
}
/* A sample's key carries the start of the pass that made it: a step that starts again counts its samples
   from 1 again, and the ticker must tell the new pass's first sample from the last one's. */
const verifierLine = (s: VerifierSample, pass: string | undefined): TickerLine => ({
  key: (pass ?? "") + "v" + s.seq, text: s.text, quoted: s.verdict === "quoted", verdict: verifierVerdict(s), kept: s.verdict !== "dropped", where: sourceWords(s),
});
export function verifyingBody(run: RunState): Extract<BriefBody, { kind: "verifying" }> {
  const v = run.verifying;
  if (v === null) return { kind: "verifying", empty: null, samples: [], bar: 0, tally: null };
  if (v.total === 0) return { kind: "verifying", empty: "No findings to check", samples: [], bar: 0, tally: null };
  return {
    kind: "verifying", empty: null, samples: v.samples.map((s) => verifierLine(s, run.startedAt.evidence_verifier)), bar: Math.min(1, v.checked / v.total),
    tally: { checked: v.checked, total: v.total, verified: v.verified, corrected: v.corrected, dropped: v.dropped },
  };
}
export function verifyTallyText(t: VerifyTally): string {
  return t.checked + " of " + t.total + " checked · " + t.verified + " verified · " + t.corrected + " corrected · " + t.dropped + " dropped";
}

/* §6.6 */
export function writerVerdict(sample: WriterSample): string {
  return sample.verdict === "backed" ? "✓ backed by " + plural(sample.findings, "finding", "findings") : "✗ removed — no verified finding says this";
}
const writerLine = (s: WriterSample, pass: string | undefined): TickerLine => ({
  key: (pass ?? "") + "w" + s.seq, text: s.text, quoted: false, verdict: writerVerdict(s), kept: s.verdict === "backed", where: s.section,
});
/* What the ticker waits on while it has no sample: the first section's draft until a part has returned;
   then the first checked sentences, for as long as a drafted sentence is unsettled (neither checked nor
   unchecked yet); once every one is settled and none was checked, that none could be (E7). A part that
   returned with no sentence drafted is not a sentence to check: while a part is still out the first
   section's line stands, since sentences may still come; once every part is back, that none was drafted
   (P3-4). Those two lines are about the sections' own phase: in `bottom_line`, while nothing at all has been
   drafted (`sentences_drafted` is 0, and it counts the bottom line's own candidates too), the one thing still
   to come is the bottom line, so the ticker says that (owner decision O2, 2026-10-01). This holds for both
   edges alike, a pass whose parts all returned with nothing drafted and a pass with no part to draft. Once
   the bottom line has drafted sentences they are what the ticker waits on: the checking line while any is
   unsettled, the all-failed line once every one is settled and none was checked, as on the sections' path.
   "Writing the bottom line…" is the in-flight line: a row that has finished (`settled`: done, or hollow after a
   loop) and is reopened shows how the step ended, so with nothing drafted it reads "No sentences were drafted to
   check" in either phase (O2 fix round 2, 2026-10-01). */
function writingPlaceholder(w: WritingState, samples: readonly TickerLine[], settled: boolean): string {
  const bottomLine = w.phase === "bottom_line";
  if (w.drafted === 0 && settled) return WRITING_NO_SENTENCES_PLACEHOLDER;
  if (w.drafted === 0 && bottomLine) return WRITING_BOTTOM_LINE_PLACEHOLDER;
  if (w.partsReturned === 0 && !bottomLine) return WRITING_PLACEHOLDER;
  if (w.drafted === 0) return w.partsReturned < w.partsTotal ? WRITING_PLACEHOLDER : WRITING_NO_SENTENCES_PLACEHOLDER;
  const unsettled = w.checked + w.unchecked < w.drafted;
  const noneChecked = samples.length === 0 && w.checked === 0 && w.unchecked > 0;
  return !unsettled && noneChecked ? WRITING_NONE_CHECKED_PLACEHOLDER : WRITING_CHECKING_PLACEHOLDER;
}
/* `settled` is true for a done or loop row (the step has ended); false, the default, for a running row. */
export function writingBody(run: RunState, settled: boolean = false): Extract<BriefBody, { kind: "writing" }> {
  const w = run.writing;
  if (w === null) return { kind: "writing", placeholder: WRITING_PLACEHOLDER, samples: [], bar: 0, tally: null };
  const samples = w.samples.map((s) => writerLine(s, run.startedAt.report_writer));
  return {
    kind: "writing", placeholder: writingPlaceholder(w, samples, settled),
    samples, bar: w.fraction,
    tally: w.drafted > 0
      ? { checked: w.checked, drafted: w.drafted, backed: w.backed, removed: w.removed, unchecked: w.unchecked, partsReturned: w.partsReturned, partsTotal: w.partsTotal }
      : null,
  };
}
export function writingTallyText(t: WritingTally): string {
  return t.checked + " of " + t.drafted + " sentences checked · ✓ " + t.backed + " backed · ✗ " + t.removed + " removed"
    + (t.partsTotal > 0 ? " · section " + t.partsReturned + " of " + t.partsTotal : "")
    + (t.unchecked > 0 ? " · " + t.unchecked + " not checked" : "");
}

/* §6.7 */
export function issueText(kinds: readonly string[]): string {
  const first = ISSUE_WORDS[kinds[0] ?? ""] ?? "an issue was raised";
  return kinds.length > 1 ? kinds.length + " issues · " + first : first;
}
/* Every interpreted note no later note replaces, in receipt order: the notes Reviewing lists. */
export function listedNotes(notes: readonly NoteState[]): NoteState[] {
  const replaced = new Set(notes.map((n) => n.replaces).filter((id): id is string => !!id));
  return notes.filter((n) => n.interpreted && !replaced.has(n.id));
}
export function noteMet(result: ReviewNoteResult | undefined): boolean {
  return !!result && result.result === "met" && (!result.steering || result.steering.result === "met");
}
export function notesClause(met: number, n: number): string {
  if (n === 0) return "";
  if (n === 1) return met === 1 ? " · your note met" : " · your note not met";
  if (n === 2 && met === 2) return " · both your notes met";
  if (met === n) return " · all " + n + " of your notes met";
  return " · " + met + " of your " + n + " notes met";
}
export function criterionLines(run: RunState, stopped: boolean): CheckLine[] {
  const r = run.reviewing;
  const before = stopped && !r.landed ? "stopped" : "reading";
  return REVIEW_CRITERIA.map(({ dimension, label }) => {
    if (!r.landed) return { key: dimension, text: label, mark: "waiting", before, fact: "", landed: false };
    const found = r.criteria?.find((c) => c.dimension === dimension);
    if (!found || found.met === null) return { key: dimension, text: label, mark: "waiting", before, fact: "not checked", landed: true };
    return found.met
      ? { key: dimension, text: label, mark: "done", before, fact: "", landed: true }
      : { key: dimension, text: label, mark: "fail", before, fact: issueText(found.kinds), landed: true };
  });
}
const halfMark = (h: ReviewHalf): CheckMark => (h.result === "met" ? "done" : h.result === "not_met" ? "fail" : "waiting");
const halfWords = (h: ReviewHalf): string => NOTE_RESULT_WORDS[h.reason] ?? h.reason;
export function reviewNoteLines(run: RunState, stopped: boolean): CheckLine[] {
  const r = run.reviewing;
  const before = stopped && !r.landed ? "stopped" : "reading";
  return listedNotes(run.notes).map((note) => {
    const text = capitalise(note.restatement ?? note.text);
    if (!r.landed) return { key: note.id, text, mark: "waiting", before, fact: "", landed: false };
    const result = r.notes.find((x) => x.noteId === note.id);
    if (!result) {
      /* §6.7: a note the review input never carried; a research note is then owed its pass. */
      return { key: note.id, text, mark: "waiting", before, fact: note.kinds.includes("new_angle") ? "researched next" : "not checked", landed: true };
    }
    if (!result.steering) return { key: note.id, text, mark: halfMark(result), before, fact: halfWords(result), landed: true };
    const marks = [halfMark(result), halfMark(result.steering)];
    const mark: CheckMark = marks.includes("fail") ? "fail" : marks.every((m) => m === "done") ? "done" : "waiting";
    return { key: note.id, text, mark, before, fact: halfWords(result) + " · " + halfWords(result.steering), landed: true };
  });
}
/* §6.7's route table: the verdict line, once the route is decided. */
export function reviewVerdict(run: RunState): string | null {
  const r = run.reviewing;
  const listed = listedNotes(run.notes);
  const met = listed.filter((n) => noteMet(r.notes.find((x) => x.noteId === n.id))).length;
  const notes = listed.length === 1 ? "note" : "notes";
  switch (r.reason) {
    case null: return null;
    case "report_accepted": return "Accepted · all 5 criteria met" + notesClause(met, listed.length);
    case "redraft_requested": return thingsToFix(r.defects) + " · sending the draft back to the writer";
    case "extra_pass_requested": return plural(r.missing, "gap", "gaps") + " to fill · going back to research";
    case "note_pass_requested": return "Going back to research your " + notes;
    case "note_redraft_requested": return "Sending the draft back to the writer for your " + notes;
    case "review_unavailable": return "The review could not be completed · publishing as partial";
    case "report_not_accepted": return notAcceptedLine(run);
    case "extra_passes_exhausted": return "Not accepted · " + plural(r.missing, "gap", "gaps") + " still open";
    default: return null;
  }
}
export function reviewingBody(run: RunState, stopped: boolean): Extract<BriefBody, { kind: "reviewing" }> {
  const r = run.reviewing;
  const verdict = reviewVerdict(run);
  return {
    kind: "reviewing",
    status: {
      texts: ["Reading the draft as a critical reader would · usually 1–3 min", "Review done · deciding what happens next…", verdict ?? ""],
      on: verdict !== null ? 2 : r.landed ? 1 : 0,
    },
    waiting: !r.landed && !stopped,
    criteria: criterionLines(run, stopped),
    notes: reviewNoteLines(run, stopped),
  };
}

/* A row's live facts line (§6.3-§6.7) as text, or null when it has none. `clock` is now, or the
   moment the reader stopped the run (null when the stop's time is not known). */
export function liveSubtitle(run: RunState, id: NodeId, clock: number | null): string | null {
  switch (id) {
    case "planner": return elapsedText(run.startedAt.planner, clock);
    case "researcher": return run.topics.length > 0 ? subtitleText(researchSubtitle(run)) : null;
    case "source_evaluator": {
      const e = run.evaluating;
      if (e === null) return "starting · not yet rated";
      if (e.toRate === 0) return "nothing new to rate";
      return e.batchesDone === 0 ? "starting · not yet rated" : e.rated + " of " + e.toRate + " rated";
    }
    case "evidence_verifier": {
      const v = run.verifying;
      if (v === null) return "starting · not yet checked";
      return v.total === 0 ? "nothing to check" : v.checked + " of " + v.total + " checked";
    }
    case "report_writer": {
      const w = run.writing;
      if (w === null) return "starting · not yet written";
      if (w.phase === "bottom_line") return "writing the bottom line";
      if (w.partsTotal === 0) return "no section to rewrite";
      /* owner decision O2 (2026-10-01): a part that ended failed counts as returned (it settled), but nothing of it
         was written: "{written} of {n} sections written", then " · {f} couldn't be written" when any failed. */
      const failed = Math.max(0, Math.min(w.partsFailed, w.partsReturned));
      return (w.partsReturned - failed) + " of " + w.partsTotal + " sections written" + (failed > 0 ? " · " + failed + " couldn't be written" : "");
    }
    case "report_reviewer": {
      const r = run.reviewing;
      /* owner decision O2 (2026-10-01): once the review has landed the row reads in the past tense, "read the draft
         in {elapsed}", frozen at the moment it landed (what follows is the route decision, not the call); a landing
         time that is not known leaves the time out rather than let one run on. Before it lands, "reading the draft ·
         {elapsed}" runs with the clock. */
      if (r.landed) {
        const landedAt = r.reviewedAt ? Date.parse(r.reviewedAt) : NaN;
        const took = Number.isNaN(landedAt) ? null : elapsedText(run.startedAt.report_reviewer, landedAt);
        return took === null ? "read the draft" : "read the draft in " + took;
      }
      const elapsed = elapsedText(run.startedAt.report_reviewer, clock);
      return elapsed === null ? "reading the draft" : "reading the draft · " + elapsed;
    }
    default: return null;
  }
}
