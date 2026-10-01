"use client";
import { memo, useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState, type MouseEvent } from "react";
import Markdown, { type Components, type Options } from "react-markdown";
import remarkGfm from "remark-gfm";
// M9: `mdast` types only, never a runtime import — they arrive transitively through
// react-markdown/remark-gfm's own `@types/mdast` dependency, so AC19's "no new dependency" holds
// without declaring `@types/mdast` in package.json.
import type { Emphasis, Paragraph, Parent, PhrasingContent, Root, RootContent, Text } from "mdast";
import type { ReportOutlineEntry } from "@/lib/api";
import { reducedMotion } from "@/lib/handoff";
import {
  CURRENT_LINE_PX, contentsModeFor, currentCard, parseEvidenceLine, reportCards, revealChip, sourceIdsOf, splitReport,
  type CardKind, type ContentsMode, type ReportCard,
} from "@/lib/report";

/* remarkCitationAnchors (spec §4.3 Report rendering): marks what the design adapts with
   hProperties the components below read — the caption after a table, the Sources list ids, the
   evidence-log link line — and turns every "[n]" whose source n exists into a link to #src-n
   (prototype index.html:1378, :1398-1415: class "cite"). A bracketed number with no matching
   source (minor 3, fix round 1) stays plain text. Hand-written recursion over node.children:
   unist-util-visit is not a dependency.
   The report renders one card per "## " section (notes-progress-report spec §7.6), so
   `sourceIds` — the "src-n" ids the Sources card's list will carry, read from the whole report
   before any card renders (lib/report.ts sourceIdsOf) — lets a citation in any card link to its
   source; a Sources list in this tree adds its own ids too. */
const textOf = (node: RootContent | PhrasingContent): string =>
  node.type === "text" ? node.value : "children" in node ? (node.children as PhrasingContent[]).map(textOf).join("") : "";
const setProps = (node: { data?: { hProperties?: Record<string, unknown> } }, props: Record<string, unknown>) => {
  node.data = node.data ?? {};
  node.data.hProperties = { ...(node.data.hProperties ?? {}), ...props };
};
const isCitationLink = (node: PhrasingContent): boolean =>
  node.type === "link" && (node.data?.hProperties as Record<string, unknown> | undefined)?.["data-cite"] !== undefined;
function citationAnchors(node: Parent, sourceIds: ReadonlySet<string>): void {
  const out: PhrasingContent[] = [];
  for (const child of node.children as PhrasingContent[]) {
    if (child.type === "text") {
      const re = /\[(\d+)\]/g;
      let last = 0;
      let m: RegExpExecArray | null;
      while ((m = re.exec(child.value)) !== null) {
        const id = `src-${m[1]}`;
        if (!sourceIds.has(id)) continue; // minor 3: no matching source — leave the bracket as plain text
        if (m.index > last) out.push({ type: "text", value: child.value.slice(last, m.index) });
        const anchor: PhrasingContent = { type: "link", url: `#${id}`, data: { hProperties: { "data-cite": m[1] } }, children: [{ type: "text", value: m[0] }] };
        setProps(anchor, { className: "cite" }); // through setProps, not the literal: hast's Properties types className as string[]
        out.push(anchor);
        last = m.index + m[0].length;
      }
      if (last < child.value.length) out.push({ type: "text", value: child.value.slice(last) } as Text);
    } else {
      if (child.type !== "link" && child.type !== "inlineCode" && "children" in child) citationAnchors(child as Parent, sourceIds);
      out.push(child);
    }
  }
  node.children = out;
}
/* I2 (fix round 1): a table cell whose rendered content is only citation link(s) — the Source
   column — gets class "n" (index.html:1384-1390: <td class="n"><a class="cite" …>). Runs after
   citationAnchors has already replaced the cell's "[n]" text with link nodes. */
function markCitationOnlyCells(table: Parent): void {
  for (const row of table.children as Parent[]) {
    if (!("children" in row)) continue;
    for (const cell of (row as Parent).children as Parent[]) {
      if (!("children" in cell) || cell.children.length === 0) continue;
      if ((cell.children as PhrasingContent[]).every(isCitationLink)) setProps(cell, { className: "n" });
    }
  }
}
export interface CitationOptions { sourceIds?: ReadonlySet<string> }
export function remarkCitationAnchors(options: CitationOptions = {}) {
  return (tree: Root) => {
    let inSources = false;
    const sourceIds = new Set<string>(options.sourceIds ?? []);
    tree.children.forEach((node, i) => {
      if (node.type === "heading" && node.depth === 2) { inSources = textOf(node) === "Sources"; return; }
      if (node.type === "list" && inSources) {
        inSources = false;
        const start = node.start ?? 1;
        setProps(node, { className: "sources" });
        node.children.forEach((li, k) => { const id = `src-${start + k}`; setProps(li, { id }); sourceIds.add(id); });
        return;
      }
      if (node.type === "paragraph") {
        const t = textOf(node);
        if (t.startsWith("How this was researched:")) setProps(node, { className: "avail", "data-role": "evidence-log-link" });
        else if (node.children.length === 1 && node.children[0].type === "emphasis" && tree.children[i - 1]?.type === "table") setProps(node, { className: "tbl-foot" });
      }
    });
    for (const node of tree.children) {
      if (node.type === "paragraph" || node.type === "list" || node.type === "table") citationAnchors(node as Parent, sourceIds);
      if (node.type === "table") markCitationOnlyCells(node as Parent);
    }
  };
}

/* An inline wrapper that renders as <span class=…>: an mdast emphasis node renamed through
   data.hName, so its children still pass through every later plugin (the citation anchors). */
const span = (className: string, children: PhrasingContent[]): Emphasis =>
  ({ type: "emphasis", data: { hName: "span", hProperties: { className: [className] } }, children });

/* remarkBottomLine (notes-progress-report spec §7.5 items 3-4, §7.6 Cards): in the Bottom line
   card the answer paragraph becomes p.lead, an emphasis-only paragraph (the assembled line) a
   muted p.b-sub, and the list ul.bl-list, each "- **{label}:** {✓ |✗ }{line}" item split into
   span.k — the mark (span.ok ✓ or span.no ✗), then the label without its colon — and span.bl-line. */
const MARK = /^\s*([✓✗])\s*/;
function bottomLineItem(paragraph: Paragraph): void {
  const [label, ...rest] = paragraph.children;
  if (label?.type !== "strong") return;
  const key: PhrasingContent[] = [];
  const first = rest[0];
  if (first?.type === "text") {
    const mark = MARK.exec(first.value);
    if (mark) key.push(span(mark[1] === "✓" ? "ok" : "no", [{ type: "text", value: mark[1] }]));
    rest[0] = { type: "text", value: first.value.replace(mark ? MARK : /^\s+/, "") };
  }
  key.push({ type: "text", value: textOf(label).replace(/:\s*$/, "") });
  paragraph.children = [span("k", key), span("bl-line", rest)];
}
export function remarkBottomLine() {
  return (tree: Root) => {
    let lead = false;
    for (const node of tree.children) {
      if (node.type === "paragraph") {
        if (node.children.length === 1 && node.children[0].type === "emphasis") setProps(node, { className: "b-sub" });
        else if (!lead) { setProps(node, { className: "lead" }); lead = true; }
      } else if (node.type === "list" && !node.ordered) {
        setProps(node, { className: "bl-list" });
        for (const item of node.children) if (item.children[0]?.type === "paragraph") bottomLineItem(item.children[0]);
      }
    }
  };
}

/* remarkKeyFigures (spec §7.4, §7.6 "Key figures on a phone", D32): in the What / Figure / Source
   table every third cell gets class kf-source, and each What cell also carries a copy of its row's
   Source cell as span.kf-src. At ≤ 480 px the CSS hides the column and shows the copy, so the
   forecast issuer and release stay visible; above that the copy is hidden. Runs before the
   citation anchors, so the copy's "[n]" links like the original's. */
export function remarkKeyFigures() {
  return (tree: Root) => {
    for (const node of tree.children) {
      if (node.type !== "table") continue;
      const [head] = node.children;
      if (!head || head.children.map((cell) => textOf(cell).trim()).join("|") !== "What|Figure|Source") continue;
      for (const row of node.children) {
        const [what, , source] = row.children;
        if (!what || !source) continue;
        setProps(source, { className: "kf-source" });
        if (row !== head) what.children.push(span("kf-src", structuredClone(source.children)));
      }
    }
  };
}

type Plugins = NonNullable<Options["remarkPlugins"]>;
function pluginsFor(kind: CardKind, sourceIds: ReadonlySet<string>): Plugins {
  const citations: Plugins[number] = [remarkCitationAnchors, { sourceIds }];
  return kind === "bottom_line" ? [remarkGfm, remarkBottomLine, citations] : [remarkGfm, remarkKeyFigures, citations];
}

/* "Evidence as of {date} · {n} sources" (+ " · {answer}" per reader answer) as spans: at ≤ 480 px
   .ev-pre and .ev-ans are hidden, leaving "{date} · {n} sources" (spec §7.6). Any other line
   ("No source could be checked.") renders whole. */
function EvidenceLine({ line }: { line: string }) {
  const parts = parseEvidenceLine(line);
  if (!parts) return <p className="cap" id="reportEvidence">{line}</p>;
  return (
    <p className="cap" id="reportEvidence">
      <span className="ev-pre">Evidence as of </span>{parts.date}<span className="ev-count"> · {parts.count}</span>
      {parts.answers.map((answer, i) => <span className="ev-ans" key={i}> · {answer}</span>)}
    </p>
  );
}

interface CardProps { card: ReportCard; sourceIds: ReadonlySet<string>; evidenceLoaded: boolean; onOpenEvidence(): void }
/* One section card (spec §7.6 Cards): a topic card opens with its "Topic i of N" eyebrow above
   its own h2; every other card prints its heading as h2.eyebrow. Each heading is the focus target
   of a contents jump (tabIndex -1). Memoised, with stable Markdown components: a new component
   function would remount the heading a jump has just focused whenever the current entry changes. */
const SectionCard = memo(function SectionCard({ card, sourceIds, evidenceLoaded, onOpenEvidence }: CardProps) {
  const headingId = `${card.id}-h`;
  const plugins = useMemo(() => pluginsFor(card.kind, sourceIds), [card.kind, sourceIds]);
  const components = useMemo((): Components => ({
    h1: () => null, // the question is the stage's own <h1>
    h2: ({ children }) => <h2 id={headingId} tabIndex={-1} className={card.kind === "topic" ? undefined : "eyebrow"}>{children}</h2>,
    p: ({ node, children, ...rest }) => {
      const role = (node?.properties as Record<string, unknown> | undefined)?.["dataRole"] ?? (node?.properties as Record<string, unknown> | undefined)?.["data-role"];
      if (role === "evidence-log-link") {
        return (
          <p className="avail" id="repEvidenceLink">How this was researched: {evidenceLoaded
            ? <button type="button" className="link" onClick={onOpenEvidence}>evidence log</button>
            : <span className="mono">evidence log</span>}</p>
        );
      }
      return <p {...rest}>{children}</p>;
    },
    table: ({ node, children }) => {
      const firstHeader = (() => {
        const thead = node?.children.find((c) => c.type === "element" && c.tagName === "thead");
        const tr = thead && "children" in thead ? thead.children.find((c) => c.type === "element") : undefined;
        const th = tr && "children" in tr ? tr.children.find((c) => c.type === "element") : undefined;
        const text = th && "children" in th ? th.children.map((c) => (c.type === "text" ? c.value : "")).join("") : "";
        return text.trim();
      })();
      return <div className="tbl-frame" {...(firstHeader === "Option" ? { "data-pinned": "" } : {})}><table className="tbl">{children}</table></div>;
    },
    a: ({ href, className, children }) => (href?.startsWith("#src-") ? <a href={href} className={className}>{children}</a> : <a href={href} className="tlink" target="_blank" rel="noopener">{children}</a>),
  }), [card.kind, headingId, evidenceLoaded, onOpenEvidence]);
  return (
    <section className="card rsec" data-kind={card.kind} id={card.id} aria-labelledby={headingId}>
      {card.kind === "topic" ? <p className="eyebrow rsec-eb">{card.eyebrow}</p> : null}
      <div className="prose">
        <Markdown remarkPlugins={plugins} components={components}>{card.markdown}</Markdown>
      </div>
    </section>
  );
});

/* What releases a contents click's hold on the current entry: the jump's scroll ending, or the
   reader scrolling on their own; PIN_MS covers a jump that never scrolls (already in place). */
const USER_SCROLL = ["wheel", "touchstart", "keydown"] as const;
const PIN_MS = 1500;

interface Props { markdown: string; outline: readonly ReportOutlineEntry[] | null; evidenceLoaded: boolean; onOpenEvidence(): void }

/* The report as section cards with a contents list (notes-progress-report spec §7.6, D16, D28):
   the server's Markdown split at its "## " headings, paired with /status's report_outline. The
   contents list is a sticky rail left of the cards when the report stage is at least
   CONTENTS_RAIL_MIN wide, otherwise a sticky row of chips above them. */
export function ReportBody({ markdown, outline, evidenceLoaded, onOpenEvidence }: Props) {
  const { evidenceLine, chunks } = useMemo(() => splitReport(markdown), [markdown]);
  const cards = useMemo(() => reportCards(chunks, outline), [chunks, outline]);
  const sourceIds = useMemo(() => sourceIdsOf(chunks), [chunks]);
  const rootRef = useRef<HTMLDivElement>(null);
  const navRef = useRef<HTMLElement>(null);
  const [contents, setContents] = useState<ContentsMode>("chips");
  const [current, setCurrent] = useState<string | null>(cards[0]?.id ?? null);
  const pinned = useRef(false);
  const unpin = useRef<(() => void) | null>(null);
  // ReportStage hands over a new inline function on every render. The cards get one stable callback that
  // calls the latest, so a parent re-render (a sessions refetch, a sidebar toggle) neither breaks the
  // cards' memo nor remounts the heading a contents jump has just focused.
  const openRef = useRef(onOpenEvidence);
  useEffect(() => { openRef.current = onOpenEvidence; }, [onOpenEvidence]);
  const openEvidence = useCallback(() => openRef.current(), []);

  // D28: measured on the report stage (the viewport less the sidebar and gutters); the CSS rail
  // rules sit under the matching @container query, so both read the same width.
  useLayoutEffect(() => {
    const stage = rootRef.current?.closest<HTMLElement>("#stage-report");
    if (!stage) return;
    const measure = () => setContents(contentsModeFor(stage.clientWidth));
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(stage);
    return () => observer.disconnect();
  }, []);

  // The current entry follows the scroll, at most once a frame, unless a click has just chosen it.
  useEffect(() => {
    let frame = 0;
    const update = () => {
      frame = 0;
      if (pinned.current) return;
      const tops = cards.map((card) => ({ id: card.id, top: document.getElementById(card.id)?.getBoundingClientRect().top ?? Number.POSITIVE_INFINITY }));
      setCurrent(currentCard(tops, CURRENT_LINE_PX));
    };
    const onScroll = () => { if (frame === 0) frame = requestAnimationFrame(update); };
    update();
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => { window.removeEventListener("scroll", onScroll); if (frame !== 0) cancelAnimationFrame(frame); };
  }, [cards]);
  useEffect(() => () => unpin.current?.(), []);

  // In the chip row the current chip is scrolled into view.
  useEffect(() => {
    const nav = navRef.current;
    const chip = current && nav ? nav.querySelector<HTMLElement>(`a[href="#${current}"]`) : null;
    if (contents === "chips" && nav && chip) revealChip(nav, chip);
  }, [contents, current]);

  const jump = (event: MouseEvent<HTMLAnchorElement>, id: string) => {
    event.preventDefault();
    const card = document.getElementById(id);
    if (!card) return;
    unpin.current?.();
    pinned.current = true;
    const release = () => {
      pinned.current = false;
      clearTimeout(timer);
      window.removeEventListener("scrollend", release);
      for (const type of USER_SCROLL) window.removeEventListener(type, release);
      unpin.current = null;
    };
    const timer = setTimeout(release, PIN_MS);
    window.addEventListener("scrollend", release);
    for (const type of USER_SCROLL) window.addEventListener(type, release, { passive: true });
    unpin.current = release;
    setCurrent(id);
    card.scrollIntoView({ behavior: reducedMotion() ? "auto" : "smooth", block: "start" });
    document.getElementById(`${id}-h`)?.focus({ preventScroll: true });
  };

  return (
    <div className="report-col" ref={rootRef}>
      {evidenceLine !== null ? <EvidenceLine line={evidenceLine} /> : null}
      <div className="rep-layout" data-contents={contents}>
        {cards.length > 0 ? (
          <nav className="rep-contents" aria-label="Report contents" ref={navRef}>
            {contents === "rail" ? <span className="eyebrow rc-h">Contents</span> : null}
            {cards.map((card) => (
              <a key={card.id} href={`#${card.id}`} aria-current={current === card.id ? "true" : undefined} onClick={(event) => jump(event, card.id)}>
                <span className="tn">{card.number ?? ""}</span><span className="rc-l">{card.label}</span>
              </a>
            ))}
          </nav>
        ) : null}
        <div className="rep-cards">
          {cards.map((card) => <SectionCard key={card.id} card={card} sourceIds={sourceIds} evidenceLoaded={evidenceLoaded} onOpenEvidence={openEvidence} />)}
        </div>
      </div>
    </div>
  );
}
