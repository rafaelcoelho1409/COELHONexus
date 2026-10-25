/** GitHub-style callouts: a blockquote that starts with `[!NOTE]`, `[!TIP]`, `[!IMPORTANT]`, `[!WARNING]` or
 * `[!CAUTION]` becomes an `<aside>` with a title line, so it can be styled and read as a note. */

import type { Blockquote, Paragraph, Parent, Root, RootContent } from "mdast";
import type { Plugin } from "unified";

const TITLES: Record<string, string> = {
  note: "Note",
  tip: "Tip",
  important: "Important",
  warning: "Warning",
  caution: "Caution",
};

const MARKER = /^\[!(note|tip|important|warning|caution)\][ \t]*\r?\n?/i;

/** The callout kind of a blockquote, with the marker removed from its first paragraph; null when it is not one. */
function claim(quote: Blockquote): string | null {
  const first = quote.children[0];
  const lead = first?.type === "paragraph" ? first.children[0] : undefined;
  if (first?.type !== "paragraph" || lead?.type !== "text") return null;
  const match = MARKER.exec(lead.value);
  const kind = match?.[1]?.toLowerCase();
  if (!match || !kind) return null;

  lead.value = lead.value.slice(match[0].length);
  if (lead.value === "") first.children.shift();
  if (first.children.length === 0) quote.children.shift();
  return kind;
}

function visit(parent: Parent) {
  for (const child of parent.children as RootContent[]) {
    if (child.type === "blockquote") {
      const kind = claim(child);
      if (kind) {
        const title: Paragraph = {
          type: "paragraph",
          data: { hName: "p", hProperties: { className: ["md-alert-title"] } },
          children: [{ type: "text", value: TITLES[kind] ?? kind }],
        };
        child.children.unshift(title);
        child.data = {
          hName: "aside",
          hProperties: { className: ["md-alert", `md-alert-${kind}`], "data-alert": kind },
        };
      }
    }
    if ("children" in child) visit(child);
  }
}

export const remarkAlerts: Plugin<[], Root> = () => (tree) => visit(tree);
