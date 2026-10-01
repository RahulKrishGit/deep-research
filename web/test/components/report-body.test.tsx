import { fireEvent, render } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ReportBody } from "../../components/ReportBody";
import type { ReportOutlineEntry } from "../../lib/api";

// notes-progress-report spec §7.5-§7.6: the published report, rendered as one card per "## " section.
const MARKDOWN = `# Where are the best lattes in San Jose?

Evidence as of 2026-09-30 · 4 sources · San Jose plus nearby South Bay · a place to go now

## Bottom line

Voyager Craft Coffee stands out [1][2].

- **Ratings and reviews:** Tripadvisor ranks Voltaire Coffee House highest [2].
- **Your note · open now:** ✓ Followed: only cafés open now
- **Your note · pastries:** ✗ Chromatic sells pastries [3]. The rest of your note was not followed in this report.
- **Your note · fire safety:** Not checked: more weight on fire safety

## Ratings and reviews

- Tripadvisor gives Voltaire Coffee House 4.7 of 5 [2], and see note [9].

## Your note: pastries at the cafés

- Chromatic Coffee serves locally sourced pastries [3].

## Key figures

| What | Figure | Source |
|---|---|---|
| Voltaire Coffee House · aggregate customer rating | 4.7 of 5 · 20 reviews | Tripadvisor [2] |
| Voyager Craft Coffee · locations | 6 | Sprudge, 2025 [4] |

*Showing 2 of 9 verified figures; all are in the evidence log.*

## What we couldn't confirm

Two pages could not be opened.

## Sources

1. a.test — [Guide](https://a.test/)
2. b.test — [Ratings](https://b.test/)
3. c.test — [Pastries](https://c.test/)
4. d.test — [Locations](https://d.test/)

How this was researched: [evidence log](report-abc-0-evidence.md)
`;

const fixed = (heading: string, kind: ReportOutlineEntry["kind"], label: string): ReportOutlineEntry =>
  ({ heading, kind, label, topic_index: null, topic_count: null, note_id: null });
const OUTLINE: ReportOutlineEntry[] = [
  fixed("Bottom line", "bottom_line", "Bottom line"),
  { heading: "Ratings and reviews", kind: "topic", label: "Ratings and reviews", topic_index: 1, topic_count: 2, note_id: null },
  { heading: "Your note: pastries at the cafés", kind: "topic", label: "Pastries (your note)", topic_index: 2, topic_count: 2, note_id: "n2" },
  fixed("Key figures", "key_figures", "Key figures"),
  fixed("What we couldn't confirm", "not_confirmed", "Not confirmed"),
  fixed("Sources", "sources", "Sources"),
];

/* jsdom lays nothing out: every card's top is 0. Give each card the top it would have in a tall page. */
function stackCards() {
  vi.spyOn(Element.prototype, "getBoundingClientRect").mockImplementation(function (this: Element) {
    const i = [...document.querySelectorAll(".rep-cards > .rsec")].indexOf(this);
    return { top: i < 0 ? 0 : 400 + i * 600, bottom: 0, left: 0, right: 0, width: 0, height: 0, x: 0, y: 0, toJSON: () => ({}) } as DOMRect;
  });
}
const show = (outline: ReportOutlineEntry[] | null = OUTLINE, markdown = MARKDOWN, onOpen = () => {}) =>
  render(<section id="stage-report"><ReportBody markdown={markdown} outline={outline} evidenceLoaded onOpenEvidence={onOpen} /></section>);

afterEach(() => {
  vi.restoreAllMocks();
  delete (Element.prototype as { scrollIntoView?: unknown }).scrollIntoView;
});

describe("ReportBody: one card per section (spec §7.6 Structure, Cards)", () => {
  it("pairs each chunk with its outline entry, keeps one h2 per card inside its .prose, and shows no Your notes block", () => {
    stackCards();
    const { container } = show();
    expect(container.querySelector("h1")).toBeNull();
    expect(container.querySelector(".reader-notes")).toBeNull();
    const cards = [...container.querySelectorAll(".report-col > .rep-layout > .rep-cards > section.card.rsec")];
    expect(cards.map((card) => [card.id, card.getAttribute("data-kind"), card.getAttribute("aria-labelledby")])).toEqual([
      ["rep-bottom-line", "bottom_line", "rep-bottom-line-h"], ["rep-topic-1", "topic", "rep-topic-1-h"],
      ["rep-topic-2", "topic", "rep-topic-2-h"], ["rep-key-figures", "key_figures", "rep-key-figures-h"],
      ["rep-not-confirmed", "not_confirmed", "rep-not-confirmed-h"], ["rep-sources", "sources", "rep-sources-h"],
    ]);
    expect(cards.map((card) => card.querySelectorAll("h2").length)).toEqual([1, 1, 1, 1, 1, 1]);
    expect(cards.map((card) => {
      const h2 = card.querySelector(":scope > .prose > h2")!;
      return [h2.textContent, h2.className, h2.id, h2.getAttribute("tabindex")];
    })).toEqual([
      ["Bottom line", "eyebrow", "rep-bottom-line-h", "-1"],
      ["Ratings and reviews", "", "rep-topic-1-h", "-1"],
      ["Your note: pastries at the cafés", "", "rep-topic-2-h", "-1"],
      ["Key figures", "eyebrow", "rep-key-figures-h", "-1"],
      ["What we couldn't confirm", "eyebrow", "rep-not-confirmed-h", "-1"],
      ["Sources", "eyebrow", "rep-sources-h", "-1"],
    ]);
    expect(cards.map((card) => card.querySelector(":scope > p.eyebrow.rsec-eb")?.textContent ?? null)).toEqual([
      null, "Topic 1 of 2", "Topic 2 of 2 · from your note", null, null, null,
    ]);
  });

  it("renders a report with no outline, or one that does not match, as unnumbered cards named by their headings", () => {
    stackCards();
    for (const outline of [null, OUTLINE.slice(0, 5)]) {
      const { container, unmount } = show(outline);
      const cards = [...container.querySelectorAll(".rep-cards > .rsec")];
      expect(cards.map((card) => [card.id, card.getAttribute("data-kind")])).toEqual(
        [1, 2, 3, 4, 5, 6].map((n) => [`rep-sec-${n}`, "section"]),
      );
      expect(cards.every((card) => card.querySelector("h2")!.className === "eyebrow")).toBe(true);
      expect(container.querySelector(".rsec-eb")).toBeNull();
      expect([...container.querySelectorAll(".rep-contents a .rc-l")].map((label) => label.textContent)).toEqual([
        "Bottom line", "Ratings and reviews", "Your note: pastries at the cafés", "Key figures", "What we couldn't confirm", "Sources",
      ]);
      unmount();
    }
  });
});

describe("the contents list (spec §7.6 Contents, Current section; D28)", () => {
  it("lists every card, Sources included, as chips below a 1310 px stage, the first card current", () => {
    stackCards();
    const { container } = show();
    expect(container.querySelector(".rep-layout")!.getAttribute("data-contents")).toBe("chips");
    const nav = container.querySelector('nav.rep-contents[aria-label="Report contents"]')!;
    expect(nav.querySelector(".rc-h")).toBeNull();
    expect([...nav.querySelectorAll("a")].map((a) => [a.getAttribute("href"), a.querySelector(".tn")!.textContent, a.querySelector(".rc-l")!.textContent])).toEqual([
      ["#rep-bottom-line", "", "Bottom line"], ["#rep-topic-1", "1", "Ratings and reviews"], ["#rep-topic-2", "2", "Pastries (your note)"],
      ["#rep-key-figures", "", "Key figures"], ["#rep-not-confirmed", "", "Not confirmed"], ["#rep-sources", "", "Sources"],
    ]);
    expect([...nav.querySelectorAll('a[aria-current="true"]')].map((a) => a.getAttribute("href"))).toEqual(["#rep-bottom-line"]);
  });

  it("is a rail headed Contents from a 1310 px report stage", () => {
    stackCards();
    let width = 1576;
    vi.spyOn(HTMLElement.prototype, "clientWidth", "get").mockImplementation(function (this: HTMLElement) {
      return this.id === "stage-report" ? width : 0;
    });
    const wide = show();
    expect(wide.container.querySelector(".rep-layout")!.getAttribute("data-contents")).toBe("rail");
    expect(wide.container.querySelector(".rep-contents > .eyebrow.rc-h")!.textContent).toBe("Contents");
    wide.unmount();
    width = 1309;
    const narrow = show();
    expect(narrow.container.querySelector(".rep-layout")!.getAttribute("data-contents")).toBe("chips");
  });

  it("jumps to a card on click: it becomes current, scrolls to the top and its heading takes focus", () => {
    stackCards();
    const scrollIntoView = vi.fn();
    Element.prototype.scrollIntoView = scrollIntoView;
    const { container } = show();
    fireEvent.click(container.querySelector('.rep-contents a[href="#rep-key-figures"]')!);
    expect(scrollIntoView).toHaveBeenCalledTimes(1);
    expect(scrollIntoView).toHaveBeenCalledWith({ behavior: "smooth", block: "start" });
    expect(scrollIntoView.mock.contexts[0]).toBe(container.querySelector("#rep-key-figures"));
    expect(document.activeElement).toBe(container.querySelector("#rep-key-figures-h"));
    expect([...container.querySelectorAll('.rep-contents a[aria-current="true"]')].map((a) => a.getAttribute("href"))).toEqual(["#rep-key-figures"]);
  });

  it("keeps the jumped-to heading focused when the parent re-renders with a new onOpenEvidence", () => {
    stackCards();
    Element.prototype.scrollIntoView = vi.fn();
    const { container, rerender } = show();
    fireEvent.click(container.querySelector('.rep-contents a[href="#rep-key-figures"]')!);
    const heading = container.querySelector("#rep-key-figures-h")!;
    expect(document.activeElement).toBe(heading);
    // ReportStage passes a new inline arrow on every render (a sessions refetch, a sidebar toggle).
    rerender(<section id="stage-report"><ReportBody markdown={MARKDOWN} outline={OUTLINE} evidenceLoaded onOpenEvidence={() => {}} /></section>);
    expect(container.querySelector("#rep-key-figures-h")).toBe(heading);
    expect(document.activeElement).toBe(heading);
  });

  it("renders no contents list for a report with no section", () => {
    stackCards();
    const { container } = show(null, "# Q\n\nNo source could be checked.\n");
    expect(container.querySelector(".rep-contents")).toBeNull();
    expect(container.querySelectorAll(".rsec")).toHaveLength(0);
    expect(container.querySelector("#reportEvidence")!.textContent).toBe("No source could be checked.");
  });

  it("jumps without smooth scrolling under reduced motion", () => {
    stackCards();
    vi.spyOn(window, "matchMedia").mockImplementation((query: string) => ({
      matches: query.includes("prefers-reduced-motion"), media: query, onchange: null,
      addListener() {}, removeListener() {}, addEventListener() {}, removeEventListener() {}, dispatchEvent: () => false,
    }));
    const scrollIntoView = vi.fn();
    Element.prototype.scrollIntoView = scrollIntoView;
    const { container } = show();
    fireEvent.click(container.querySelector('.rep-contents a[href="#rep-sources"]')!);
    expect(scrollIntoView).toHaveBeenCalledWith({ behavior: "auto", block: "start" });
  });
});

describe("the bottom line card (spec §7.5 items 3-4, §7.6 Cards)", () => {
  it("prints the answer as a lead, then one row per topic line and note line with its mark in the key", () => {
    stackCards();
    const { container } = show();
    const card = container.querySelector("#rep-bottom-line")!;
    expect(card.querySelector(".prose > p.lead")!.textContent).toBe("Voyager Craft Coffee stands out [1][2].");
    const rows = [...card.querySelectorAll(".prose > ul.bl-list > li")];
    expect(rows.map((li) => [
      li.querySelector(":scope > .k")!.textContent, li.querySelector(".k > .ok, .k > .no")?.className ?? null,
      li.querySelector(":scope > .bl-line")!.textContent,
    ])).toEqual([
      ["Ratings and reviews", null, "Tripadvisor ranks Voltaire Coffee House highest [2]."],
      ["✓Your note · open now", "ok", "Followed: only cafés open now"],
      ["✗Your note · pastries", "no", "Chromatic sells pastries [3]. The rest of your note was not followed in this report."],
      ["Your note · fire safety", null, "Not checked: more weight on fire safety"],
    ]);
    expect(rows[2].querySelector('.bl-line a.cite[href="#src-3"]')).not.toBeNull();
  });

  it("prints an assembled bottom line as a muted line above its topic lines, with no lead", () => {
    stackCards();
    const markdown = "# Q\n\nEvidence as of 2026-09-30 · 1 source\n\n## Bottom line\n\n"
      + "*Assembled from the sections below; the summary could not be written this time.*\n\n"
      + "- **Part one:** Agency One reports part one [1].\n\n## Sources\n\n1. a.test — [A](https://a.test/)\n";
    const { container } = show([fixed("Bottom line", "bottom_line", "Bottom line"), fixed("Sources", "sources", "Sources")], markdown);
    const card = container.querySelector("#rep-bottom-line")!;
    expect(card.querySelector("p.lead")).toBeNull();
    expect(card.querySelector("p.b-sub")!.textContent).toBe("Assembled from the sections below; the summary could not be written this time.");
    expect([...card.querySelectorAll(".bl-list > li")].map((li) => [li.querySelector(".k")!.textContent, li.querySelector(".bl-line")!.textContent])).toEqual([
      ["Part one", "Agency One reports part one [1]."],
    ]);
  });
});

describe("citations, tables and the evidence line across cards", () => {
  it("links [n] in every card to its source in the Sources card; a number with no source stays plain text", () => {
    stackCards();
    const { container } = show();
    expect([...container.querySelectorAll("#rep-sources ol.sources > li")].map((li) => li.id)).toEqual(["src-1", "src-2", "src-3", "src-4"]);
    expect(container.querySelector('#rep-topic-1 a.cite[href="#src-2"]')!.textContent).toBe("[2]");
    expect(container.querySelector('#rep-topic-2 a.cite[href="#src-3"]')).not.toBeNull();
    expect(container.querySelector('a[href="#src-9"]')).toBeNull();
    expect(container.querySelector("#rep-topic-1 li")!.textContent).toContain("[9]");
    expect(container.querySelector("#rep-bottom-line p.lead")!.textContent).toContain("[1][2]"); // a run stays a run
    for (const a of container.querySelectorAll("#rep-sources ol li a")) {
      expect([a.getAttribute("target"), a.getAttribute("rel")]).toEqual(["_blank", "noopener"]);
    }
  });

  it("gives the Key figures table's Source column a phone copy under each What cell", () => {
    stackCards();
    const { container } = show();
    const frame = container.querySelector("#rep-key-figures .tbl-frame")!;
    expect(frame.hasAttribute("data-pinned")).toBe(false);
    const table = frame.querySelector("table.tbl")!;
    expect([...table.querySelectorAll("th, td")].filter((cell) => cell.classList.contains("kf-source")).map((cell) => cell.textContent)).toEqual([
      "Source", "Tripadvisor [2]", "Sprudge, 2025 [4]",
    ]);
    const rows = [...table.querySelectorAll("tbody tr")];
    expect(rows.map((row) => row.children[0].querySelector(".kf-src")!.textContent)).toEqual(["Tripadvisor [2]", "Sprudge, 2025 [4]"]);
    expect(rows[0].children[0].querySelector('.kf-src a.cite[href="#src-2"]')).not.toBeNull();
    expect(table.querySelector("thead .kf-src")).toBeNull();
    expect(frame.nextElementSibling!.className).toContain("tbl-foot");
  });

  it("frames an options table pinned, with its citation-only Source cells marked", () => {
    stackCards();
    const markdown = "# Q\n\nEvidence as of 2026-09-30 · 2 sources\n\n## Bottom line\n\nLFP leads [1].\n\n"
      + "## Options compared\n\n| Option | Energy density | Recommended by |\n|---|---|---|\n| LFP | lower | [1] |\n| NMC | higher | [2] |\n\n"
      + "*Options compared on the parts the plan named; an empty cell reads not stated.*\n\n"
      + "## Sources\n\n1. a.test — [A](https://a.test/)\n2. b.test — [B](https://b.test/)\n";
    const outline = [fixed("Bottom line", "bottom_line", "Bottom line"), fixed("Options compared", "options", "Options compared"), fixed("Sources", "sources", "Sources")];
    const { container } = show(outline, markdown);
    expect(container.querySelector("#rep-options h2.eyebrow")!.textContent).toBe("Options compared");
    const frame = container.querySelector("#rep-options .tbl-frame")!;
    expect(frame.hasAttribute("data-pinned")).toBe(true);
    expect([...frame.querySelectorAll("tbody tr")].map((tr) => tr.lastElementChild!.classList.contains("n"))).toEqual([true, true]);
    expect(frame.querySelector(".kf-src, .kf-source")).toBeNull();
  });

  it("lifts the evidence line out of the cards as spans the phone can shorten", () => {
    stackCards();
    const { container } = show();
    const line = container.querySelector(".report-col > p.cap#reportEvidence")!;
    expect(line.textContent).toBe("Evidence as of 2026-09-30 · 4 sources · San Jose plus nearby South Bay · a place to go now");
    expect([line.querySelector(".ev-pre")!.textContent, line.querySelector(".ev-count")!.textContent]).toEqual(["Evidence as of ", " · 4 sources"]);
    expect([...line.querySelectorAll(".ev-ans")].map((part) => part.textContent)).toEqual([" · San Jose plus nearby South Bay", " · a place to go now"]);
    expect(container.querySelector(".rep-cards")!.textContent).not.toContain("Evidence as of");
  });

  it("renders a line that is not 'Evidence as of …' whole", () => {
    stackCards();
    const { container } = show(null, "# Q\n\nNo source could be checked.\n\n## Bottom line\n\nNothing could be checked.\n");
    const line = container.querySelector("#reportEvidence")!;
    expect(line.textContent).toBe("No source could be checked.");
    expect(line.querySelector("span")).toBeNull();
  });

  it("keeps the evidence-log link in the Sources card: a button once the log has loaded, muted words before", () => {
    stackCards();
    const onOpen = vi.fn();
    const { container, unmount } = show(OUTLINE, MARKDOWN, onOpen);
    const link = container.querySelector("#rep-sources p.avail#repEvidenceLink button.link")!;
    expect(link.textContent).toBe("evidence log");
    fireEvent.click(link);
    expect(onOpen).toHaveBeenCalledTimes(1);
    unmount();
    const loading = render(<ReportBody markdown={MARKDOWN} outline={OUTLINE} evidenceLoaded={false} onOpenEvidence={() => {}} />);
    expect(loading.container.querySelector("#repEvidenceLink button")).toBeNull();
    expect(loading.container.querySelector("#repEvidenceLink")!.textContent).toBe("How this was researched: evidence log");
  });

  it("opens the evidence log through the latest onOpenEvidence after a re-render", () => {
    stackCards();
    const first = vi.fn();
    const latest = vi.fn();
    const { container, rerender } = show(OUTLINE, MARKDOWN, first);
    rerender(<section id="stage-report"><ReportBody markdown={MARKDOWN} outline={OUTLINE} evidenceLoaded onOpenEvidence={latest} /></section>);
    fireEvent.click(container.querySelector("#repEvidenceLink button.link")!);
    expect([first.mock.calls.length, latest.mock.calls.length]).toEqual([0, 1]);
  });
});
