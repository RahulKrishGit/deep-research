import { describe, expect, it } from "vitest";
import type { ReportOutlineEntry } from "../lib/api";
import {
  CONTENTS_RAIL_MIN, CURRENT_LINE_PX, contentsModeFor, currentCard, parseEvidenceLine, reportCards, revealChip,
  sourceIdsOf, splitReport,
} from "../lib/report";

const MARKDOWN = [
  "# Where are the best lattes?", "", "Evidence as of 2026-09-30 · 3 sources · San Jose · Top 3", "",
  "## Bottom line", "", "Voyager stands out [1].", "", "- **Ratings:** Voltaire rates 4.7 [2].", "",
  "## Ratings and reviews", "", "- Voltaire rates 4.7 [2].", "",
  "## Your note: pastries at the cafés", "", "- Chromatic sells pastries [3].", "",
  "## Key figures", "", "| What | Figure | Source |", "|---|---|---|", "| Voltaire · rating | 4.7 of 5 | Tripadvisor [2] |", "",
  "## What we couldn't confirm", "", "Nothing else.", "",
  "## Sources", "", "1. a.test — [A](https://a.test/)", "2. b.test — [B](https://b.test/)", "3. c.test — [C](https://c.test/)", "",
  "How this was researched: [evidence log](report-s-evidence.md)", "",
].join("\n");

const entry = (heading: string, kind: ReportOutlineEntry["kind"], label: string, topic: [number, number] | null = null, noteId: string | null = null): ReportOutlineEntry =>
  ({ heading, kind, label, topic_index: topic?.[0] ?? null, topic_count: topic?.[1] ?? null, note_id: noteId });
const OUTLINE: ReportOutlineEntry[] = [
  entry("Bottom line", "bottom_line", "Bottom line"),
  entry("Ratings and reviews", "topic", "Ratings", [1, 2]),
  entry("Your note: pastries at the cafés", "topic", "Pastries (your note)", [2, 2], "n1"),
  entry("Key figures", "key_figures", "Key figures"),
  entry("What we couldn't confirm", "not_confirmed", "Not confirmed"),
  entry("Sources", "sources", "Sources"),
];

describe("splitReport (notes-progress-report spec §7.6 Chunks)", () => {
  it("splits at every '## ' line and lifts the evidence line out of the lead", () => {
    const { evidenceLine, chunks } = splitReport(MARKDOWN.replace(/\n/g, "\r\n"));
    expect(evidenceLine).toBe("Evidence as of 2026-09-30 · 3 sources · San Jose · Top 3");
    expect(chunks.map((chunk) => chunk.heading)).toEqual([
      "Bottom line", "Ratings and reviews", "Your note: pastries at the cafés", "Key figures", "What we couldn't confirm", "Sources",
    ]);
    expect(chunks[0].markdown).toBe("## Bottom line\n\nVoyager stands out [1].\n\n- **Ratings:** Voltaire rates 4.7 [2].\n");
    expect(chunks.at(-1)!.markdown.trimEnd().endsWith("How this was researched: [evidence log](report-s-evidence.md)")).toBe(true);
  });

  it("has no evidence line when the lead holds only the question", () => {
    expect(splitReport("# Q\n\n## Bottom line\n\nA.\n").evidenceLine).toBeNull();
  });
});

describe("reportCards pairs chunks with report_outline by position", () => {
  it("numbers topics 'Topic i of N', marks a note's topic, and gives every card its contents label", () => {
    const cards = reportCards(splitReport(MARKDOWN).chunks, OUTLINE);
    expect(cards.map((card) => [card.id, card.kind, card.label, card.eyebrow, card.number])).toEqual([
      ["rep-bottom-line", "bottom_line", "Bottom line", "Bottom line", null],
      ["rep-topic-1", "topic", "Ratings", "Topic 1 of 2", 1],
      ["rep-topic-2", "topic", "Pastries (your note)", "Topic 2 of 2 · from your note", 2],
      ["rep-key-figures", "key_figures", "Key figures", "Key figures", null],
      ["rep-not-confirmed", "not_confirmed", "Not confirmed", "What we couldn't confirm", null],
      ["rep-sources", "sources", "Sources", "Sources", null],
    ]);
  });

  it("falls back to unnumbered cards named by their headings without an outline or when a heading differs", () => {
    const chunks = splitReport(MARKDOWN).chunks;
    const renamed = OUTLINE.map((item, i) => (i === 1 ? { ...item, heading: "Ratings" } : item));
    for (const outline of [null, undefined, OUTLINE.slice(1), renamed]) {
      const cards = reportCards(chunks, outline);
      expect(cards.map((card) => [card.id, card.kind, card.label, card.eyebrow, card.number])).toEqual(
        chunks.map((chunk, i) => [`rep-sec-${i + 1}`, "section", chunk.heading, chunk.heading, null]),
      );
    }
  });
});

describe("the evidence line's parts (spec §7.5, §7.6)", () => {
  it("splits the date, the count and one part per reader answer", () => {
    expect(parseEvidenceLine("Evidence as of 2026-09-30 · 29 sources · San Jose plus nearby South Bay · a place to go now")).toEqual({
      date: "2026-09-30", count: "29 sources", answers: ["San Jose plus nearby South Bay", "a place to go now"],
    });
    expect(parseEvidenceLine("Evidence as of 2026-09-30 · 1 source")).toEqual({ date: "2026-09-30", count: "1 source", answers: [] });
  });

  it("does not split any other line", () => {
    expect(parseEvidenceLine("No source could be checked.")).toBeNull();
  });
});

describe("sourceIdsOf reads the Sources card's ids before any card renders", () => {
  it("returns one src-n per numbered source", () => {
    expect([...sourceIdsOf(splitReport(MARKDOWN).chunks)]).toEqual(["src-1", "src-2", "src-3"]);
    expect(sourceIdsOf(splitReport("# Q\n\n## Bottom line\n\nA.\n").chunks).size).toBe(0);
  });
});

describe("the contents list (D28)", () => {
  it("is a rail from a 1310 px report stage, chips below it", () => {
    expect(CONTENTS_RAIL_MIN).toBe(176 + 32 + 770 + 32 + 300);
    expect([contentsModeFor(1576), contentsModeFor(1310), contentsModeFor(1309), contentsModeFor(1224), contentsModeFor(358)]).toEqual([
      "rail", "rail", "chips", "chips", "chips",
    ]);
  });

  it("marks the last card whose top has passed the line, else the first", () => {
    expect(CURRENT_LINE_PX).toBe(56 + 56 + 16);
    const tops = (...values: number[]) => values.map((top, i) => ({ id: `c${i + 1}`, top }));
    expect(currentCard(tops(300, 900, 1500), CURRENT_LINE_PX)).toBe("c1");
    expect(currentCard(tops(-400, 128.6, 700), CURRENT_LINE_PX)).toBe("c2");
    expect(currentCard(tops(-900, -300, 40), CURRENT_LINE_PX)).toBe("c3");
    expect(currentCard([], CURRENT_LINE_PX)).toBeNull();
  });

  it("scrolls the chip row just enough to show the current chip", () => {
    const nav = { scrollLeft: 0, clientWidth: 300 } as HTMLElement;
    const chip = (offsetLeft: number, offsetWidth: number) => ({ offsetLeft, offsetWidth }) as HTMLElement;
    revealChip(nav, chip(500, 120));
    expect(nav.scrollLeft).toBe(320);
    revealChip(nav, chip(400, 100));
    expect(nav.scrollLeft).toBe(320);
    revealChip(nav, chip(40, 100));
    expect(nav.scrollLeft).toBe(40);
  });
});
