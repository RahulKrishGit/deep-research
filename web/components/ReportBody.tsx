"use client";
import Markdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";
// M9: `mdast` types only, never a runtime import — they arrive transitively through
// react-markdown/remark-gfm's own `@types/mdast` dependency, so AC19's "no new dependency" holds
// without declaring `@types/mdast` in package.json.
import type { Parent, PhrasingContent, Root, RootContent, Text } from "mdast";
import type { ReaderNoteRecord } from "@/lib/api";
import { noteCaption } from "@/lib/notes";

/* remarkCitationAnchors (spec §4.3 Report rendering): marks what the design adapts with
   hProperties the components below read — the evidence line, the caption after a table, the
   Sources list ids, the evidence-log link line — and turns every "[n]" whose source n exists into
   a link to #src-n (prototype index.html:1378, :1398-1415: class "cite"). A bracketed number with
   no matching source (minor 3, fix round 1) stays plain text. Hand-written recursion over
   node.children: unist-util-visit is not a dependency.
   sourceIds is the set of "src-n" ids the Sources list actually assigned, gathered before any
   citation is linked (the Sources list is walked in the same pass, ahead of this recursion). */
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
export function remarkCitationAnchors() {
  return (tree: Root) => {
    let inSources = false;
    const sourceIds = new Set<string>();
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
        if (/^Evidence as of |^No source could be checked\.$/.test(t)) setProps(node, { className: "avail", "data-role": "evidence-line" });
        else if (t.startsWith("How this was researched:")) setProps(node, { className: "avail", "data-role": "evidence-log-link" });
        else if (node.children.length === 1 && node.children[0].type === "emphasis" && tree.children[i - 1]?.type === "table") setProps(node, { className: "tbl-foot" });
      }
    });
    for (const node of tree.children) {
      if (node.type === "paragraph" || node.type === "list" || node.type === "table") citationAnchors(node as Parent, sourceIds);
      if (node.type === "table") markCitationOnlyCells(node as Parent);
    }
  };
}

interface Props { markdown: string; evidenceLoaded: boolean; onOpenEvidence(): void; notes?: readonly ReaderNoteRecord[] }

export function ReportBody({ markdown, evidenceLoaded, onOpenEvidence, notes = [] }: Props) {
  const components: Components = {
    h1: () => null, // the question is the stage's own <h1>
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
  };
  return (
    <article className="card stack" style={{ gap: "var(--space-5)" }}>
      {/* live-briefs spec §4.7: "Your notes", inside the report card and above the prose — not a card
          of its own, and outside .prose — with each note's outcome as a caption. */}
      {notes.length > 0 ? (
        <section className="reader-notes" id="readerNotes" aria-labelledby="readerNotesH">
          <h2 className="eyebrow" id="readerNotesH">Your notes</h2>
          <ul className="rn-list">
            {notes.map((note) => (
              <li key={note.note_id} data-outcome={note.outcome}><span className="rn-text">{note.text}</span> <span className="cap">{noteCaption(note)}</span></li>
            ))}
          </ul>
        </section>
      ) : null}
      <div className="prose">
        <Markdown remarkPlugins={[remarkGfm, remarkCitationAnchors]} components={components}>{markdown}</Markdown>
      </div>
    </article>
  );
}
