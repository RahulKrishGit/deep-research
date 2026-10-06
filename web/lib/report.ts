// The report as cards: the server's Markdown split at its "## "
// headings and paired, by position, with /status's report_outline. Pure: no DOM but the two
// measuring helpers at the foot, which take the elements they measure.
import type { ReportOutlineEntry, ReportOutlineKind } from "./api";

/* The report stage's width at which the contents list becomes a rail: 176 px of rail, the
   32 px gap, the 770 px cards, the 32 px gap and the 300 px Review rail. */
export const CONTENTS_RAIL_MIN = 1310;
export type ContentsMode = "rail" | "chips";
export const contentsModeFor = (stageWidth: number): ContentsMode => (stageWidth >= CONTENTS_RAIL_MIN ? "rail" : "chips");
/* var(--topbar) + 56px (the chip row) + var(--space-4): a card whose top has passed this line is
   the current one; the cards' scroll-margin-top is the same length (globals.css). */
export const CURRENT_LINE_PX = 128;

export interface ReportChunk { heading: string; markdown: string }
export interface ReportParts { evidenceLine: string | null; chunks: ReportChunk[] }

/* Split at every line that starts with "## ". The lines before the first heading hold "# question"
   (the stage prints the question itself) and the evidence line. */
export function splitReport(markdown: string): ReportParts {
  const lead: string[] = [];
  const chunks: ReportChunk[] = [];
  for (const line of markdown.replace(/\r\n/g, "\n").split("\n")) {
    if (line.startsWith("## ")) chunks.push({ heading: line.slice(3), markdown: line });
    else if (chunks.length === 0) lead.push(line);
    else chunks[chunks.length - 1].markdown += "\n" + line;
  }
  const evidenceLine = lead.find((line) => line.trim() !== "" && !line.startsWith("# ")) ?? null;
  return { evidenceLine, chunks };
}

/* "section" is a card the outline could not name: no outline, or one that does not match. */
export type CardKind = ReportOutlineKind | "section";
export interface ReportCard {
  id: string; kind: CardKind; heading: string; markdown: string;
  label: string; eyebrow: string; number: number | null;
}

const FIXED_IDS: Record<Exclude<ReportOutlineKind, "topic">, string> = {
  bottom_line: "rep-bottom-line", key_figures: "rep-key-figures", options: "rep-options",
  not_confirmed: "rep-not-confirmed", sources: "rep-sources",
};

/* One card per chunk. With an outline whose headings match the chunks one to one, a topic card is
   numbered "Topic i of N" (" · from your note" for a note's topic) and every entry gives its
   contents label; otherwise every card shows its heading as its eyebrow, unnumbered. */
export function reportCards(chunks: readonly ReportChunk[], outline: readonly ReportOutlineEntry[] | null | undefined): ReportCard[] {
  if (!outline || outline.length !== chunks.length || outline.some((entry, i) => entry.heading !== chunks[i].heading)) {
    return chunks.map((chunk, i): ReportCard => ({
      heading: chunk.heading, markdown: chunk.markdown, id: `rep-sec-${i + 1}`, kind: "section",
      label: chunk.heading, eyebrow: chunk.heading, number: null,
    }));
  }
  const entries = outline;
  return chunks.map((chunk, i): ReportCard => {
    const base = { heading: chunk.heading, markdown: chunk.markdown };
    const entry = entries[i];
    if (entry.kind === "topic") {
      const fromNote = entry.note_id !== null;
      return {
        ...base, id: `rep-topic-${entry.topic_index}`, kind: "topic", label: entry.label,
        eyebrow: `Topic ${entry.topic_index} of ${entry.topic_count}${fromNote ? " · from your note" : ""}`, number: entry.topic_index,
      };
    }
    return { ...base, id: FIXED_IDS[entry.kind], kind: entry.kind, label: entry.label, eyebrow: chunk.heading, number: null };
  });
}

/* "Evidence as of {date} · {n} source(s)" then " · {answer}" per reader answer; any
   other line ("No source could be checked.") is not split. */
export interface EvidenceParts { date: string; count: string; answers: string[] }
export function parseEvidenceLine(line: string): EvidenceParts | null {
  const prefix = "Evidence as of ";
  if (!line.startsWith(prefix)) return null;
  const [date, count, ...answers] = line.slice(prefix.length).split(" · ");
  return date && count ? { date, count, answers } : null;
}

/* The "src-n" ids the Sources card's list will carry, read before any card renders, so a citation
   in any card can link to its source. */
export function sourceIdsOf(chunks: readonly ReportChunk[]): Set<string> {
  const ids = new Set<string>();
  const sources = chunks.find((chunk) => chunk.heading === "Sources");
  for (const line of sources?.markdown.split("\n") ?? []) {
    const match = /^(\d+)\.\s/.exec(line);
    if (match) ids.add(`src-${match[1]}`);
  }
  return ids;
}

/* The current card: the last whose top has passed `line` px below the viewport's top (1 px allowed
   for sub-pixel rounding, so a card a jump left exactly on the line counts), else the first. */
export function currentCard(tops: readonly { id: string; top: number }[], line: number): string | null {
  let current = tops[0]?.id ?? null;
  for (const { id, top } of tops) if (top <= line + 1) current = id;
  return current;
}

/* Scroll a chip row sideways just enough to show `chip` (its offsetParent is the sticky row). */
export function revealChip(nav: HTMLElement, chip: HTMLElement): void {
  const start = chip.offsetLeft;
  const end = start + chip.offsetWidth;
  if (start < nav.scrollLeft) nav.scrollLeft = start;
  else if (end > nav.scrollLeft + nav.clientWidth) nav.scrollLeft = end - nav.clientWidth;
}
