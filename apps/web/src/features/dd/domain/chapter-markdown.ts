/** What the study reader does to a synthesized chapter before showing it: drop its leading title (the page shows
 * it once, above) and the inline "Contents" list (the outline beside the page replaces it), fold each "Sources for this section" block into a collapsed
 * `<details>` whose file names open the ingested page, and undo a wrapper code fence a model sometimes adds. */

import type { Paragraph, Parent, Root, RootContent } from "mdast";
import { toString as plainText } from "mdast-util-to-string";
import type { Plugin } from "unified";

/** The folded block: a `<details>` with a summary line and the list of sources. */
interface SourcesFold extends Parent {
  type: "sourcesFold";
  children: RootContent[];
}

declare module "mdast" {
  interface RootContentMap {
    sourcesFold: SourcesFold;
  }
}

/** The page prints the chapter's title above the text, so the document's own leading `# Title` would read twice. */
const remarkDropTitle: Plugin<[], Root> = () => (tree) => {
  const first = tree.children[0];
  if (first?.type === "heading" && first.depth === 1) tree.children = tree.children.slice(1);
};

/** Drops the `## Contents` heading, the list(s) under it and the divider after them. */
const remarkStripContents: Plugin<[], Root> = () => (tree) => {
  const kept: RootContent[] = [];
  const nodes = tree.children;
  for (let index = 0; index < nodes.length; index += 1) {
    const node = nodes[index];
    if (!node) continue;
    if (node.type === "heading" && node.depth === 2 && plainText(node).trim().toLowerCase() === "contents") {
      while (nodes[index + 1]?.type === "list") index += 1;
      if (nodes[index + 1]?.type === "thematicBreak") index += 1;
      continue;
    }
    kept.push(node);
  }
  tree.children = kept;
};

const SOURCES_LABEL = /^sources for this section:?$/i;

/** The first inline code of a source line is its file name: tag it so the reader can make it a button. */
function tagSourceFiles(list: Parent) {
  for (const item of list.children as RootContent[]) {
    if (item.type !== "listItem") continue;
    const code = findInlineCode(item);
    if (code) {
      const basename = code.value.trim();
      code.data = { hProperties: { "data-source": basename } };
    }
  }
}

function findInlineCode(parent: Parent): Extract<RootContent, { type: "inlineCode" }> | null {
  for (const child of parent.children as RootContent[]) {
    if (child.type === "inlineCode") return child;
    if ("children" in child) {
      const found = findInlineCode(child);
      if (found) return found;
    }
  }
  return null;
}

/** "Sources for this section:" + its list → one collapsed `<details>` with a count in the summary. */
const remarkFoldSources: Plugin<[], Root> = () => (tree) => {
  const folded: RootContent[] = [];
  const nodes = tree.children;
  for (let index = 0; index < nodes.length; index += 1) {
    const node = nodes[index];
    if (!node) continue;
    if (node.type !== "paragraph" || !SOURCES_LABEL.test(plainText(node).trim())) {
      folded.push(node);
      continue;
    }
    const list = nodes[index + 1];
    const sources = list?.type === "list" ? list : null;
    if (sources) {
      index += 1;
      tagSourceFiles(sources);
    }
    const count = sources?.children.length ?? 0;
    const summary: Paragraph = {
      type: "paragraph",
      data: { hName: "summary" },
      children: [{ type: "text", value: `Sources for this section${count ? ` (${count})` : ""}` }],
    };
    folded.push({
      type: "sourcesFold",
      data: { hName: "details", hProperties: { className: ["study-sources"] } },
      children: sources ? [summary, sources] : [summary],
    });
  }
  tree.children = folded;
};

/** Stable, so the renderer's memoised pipeline is not rebuilt on every render. */
export const CHAPTER_PLUGINS = [remarkDropTitle, remarkStripContents, remarkFoldSources];

const isFence = (line: string | undefined) => /^\s*(```|~~~)/.test(line ?? "");
const isBareFence = (line: string | undefined) =>
  isFence(line) && (line ?? "").replace(/^[\s`~]+/, "").trim() === "";

/**
 * A chapter always starts with a `#` heading, so a code fence on its first line is never legitimate: it pairs
 * with the last fence and swallows the whole chapter into one code block. Drop that wrapper and its closer.
 */
export function unwrapChapterFence(markdown: string): string {
  const lines = markdown.split("\n");
  if (!isFence(lines[0])) return markdown;
  let first = 1;
  while (first < lines.length && !lines[first]?.trim()) first += 1;
  if (!/^\s*#/.test(lines[first] ?? "")) return markdown;

  lines.shift();
  let last = lines.length - 1;
  while (last >= 0 && !lines[last]?.trim()) last -= 1;
  if (last >= 0 && isBareFence(lines[last])) lines.splice(last, 1);
  return lines.join("\n");
}
