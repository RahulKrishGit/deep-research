"use client";
import Markdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";
import type { Parent, PhrasingContent, Root, RootContent, Text } from "mdast";

/* remarkCitationAnchors (spec §4.3 Report rendering): marks what the design adapts with
   hProperties the components below read — the evidence line, the caption after a table, the
   Sources list ids, the evidence-log link line — and turns every "[n]" into a link to #src-n.
   Hand-written recursion over node.children: unist-util-visit is not a dependency. */
const textOf = (node: RootContent | PhrasingContent): string =>
  node.type === "text" ? node.value : "children" in node ? (node.children as PhrasingContent[]).map(textOf).join("") : "";
const setProps = (node: { data?: { hProperties?: Record<string, unknown> } }, props: Record<string, unknown>) => {
  node.data = node.data ?? {};
  node.data.hProperties = { ...(node.data.hProperties ?? {}), ...props };
};
function citationAnchors(node: Parent): void {
  const out: PhrasingContent[] = [];
  for (const child of node.children as PhrasingContent[]) {
    if (child.type === "text") {
      const re = /\[(\d+)\]/g;
      let last = 0;
      let m: RegExpExecArray | null;
      while ((m = re.exec(child.value)) !== null) {
        if (m.index > last) out.push({ type: "text", value: child.value.slice(last, m.index) });
        out.push({ type: "link", url: `#src-${m[1]}`, data: { hProperties: { "data-cite": m[1] } }, children: [{ type: "text", value: m[0] }] });
        last = m.index + m[0].length;
      }
      if (last < child.value.length) out.push({ type: "text", value: child.value.slice(last) } as Text);
    } else {
      if (child.type !== "link" && child.type !== "inlineCode" && "children" in child) citationAnchors(child as Parent);
      out.push(child);
    }
  }
  node.children = out;
}
export function remarkCitationAnchors() {
  return (tree: Root) => {
    let inSources = false;
    tree.children.forEach((node, i) => {
      if (node.type === "heading" && node.depth === 2) { inSources = textOf(node) === "Sources"; return; }
      if (node.type === "list" && inSources) {
        inSources = false;
        const start = node.start ?? 1;
        setProps(node, { className: "sources" });
        node.children.forEach((li, k) => setProps(li, { id: `src-${start + k}` }));
        return;
      }
      if (node.type === "paragraph") {
        const t = textOf(node);
        if (/^Evidence as of |^No source could be checked\.$/.test(t)) setProps(node, { className: "avail", "data-role": "evidence-line" });
        else if (t.startsWith("How this was researched:")) setProps(node, { className: "avail", "data-role": "evidence-log-link" });
        else if (node.children.length === 1 && node.children[0].type === "emphasis" && tree.children[i - 1]?.type === "table") setProps(node, { className: "tbl-foot" });
      }
    });
    for (const node of tree.children) if (node.type === "paragraph" || node.type === "list" || node.type === "table") citationAnchors(node as Parent);
  };
}

interface Props { markdown: string; evidenceLoaded: boolean; onOpenEvidence(): void }

export function ReportBody({ markdown, evidenceLoaded, onOpenEvidence }: Props) {
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
    a: ({ href, children }) => (href?.startsWith("#src-") ? <a href={href}>{children}</a> : <a href={href} className="tlink" target="_blank" rel="noopener">{children}</a>),
  };
  return (
    <article className="card stack" style={{ gap: "var(--space-5)" }}>
      <div className="prose">
        <Markdown remarkPlugins={[remarkGfm, remarkCitationAnchors]} components={components}>{markdown}</Markdown>
      </div>
    </article>
  );
}
