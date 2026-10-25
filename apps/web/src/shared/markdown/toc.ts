/** The outline of a markdown document: its h2/h3 headings with the same ids `rehype-slug` gives the rendered
 * headings, so an outline link always lands on its heading. */

import GithubSlugger from "github-slugger";
import type { Parent, Root, RootContent } from "mdast";
import { toString as plainText } from "mdast-util-to-string";
import remarkGfm from "remark-gfm";
import remarkMath from "remark-math";
import remarkParse from "remark-parse";
import { type PluggableList, unified } from "unified";

import { normalizeMath } from "./math";

export type TocEntry = { level: 2 | 3; text: string; id: string };

function collect(parent: Parent, slugger: GithubSlugger, entries: TocEntry[]) {
  for (const child of parent.children as RootContent[]) {
    if (child.type === "heading") {
      const text = plainText(child);
      const id = slugger.slug(text); // every heading advances the slugger, as it does when rendering
      if ((child.depth === 2 || child.depth === 3) && text.trim()) {
        entries.push({ level: child.depth, text: text.trim(), id });
      }
    }
    if ("children" in child) collect(child, slugger, entries);
  }
}

/** `plugins` are the same remark plugins the document is rendered with, so the outline matches what is shown
 * (a plugin that drops a "Contents" block drops it from the outline too). */
export function markdownToc(markdown: string, plugins: PluggableList = []): TocEntry[] {
  const processor = unified().use(remarkParse).use(remarkGfm).use(remarkMath).use(plugins);
  const tree: Root = processor.parse(normalizeMath(markdown));
  processor.runSync(tree); // the plugins edit the tree in place
  const entries: TocEntry[] = [];
  collect(tree, new GithubSlugger(), entries);
  return entries;
}
