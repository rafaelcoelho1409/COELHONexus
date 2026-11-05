/** Searching every ingested page of a framework: a hit is a page title, a heading, or a match somewhere in the text, in
 * that order of relevance. A match in the text shows the words around it. */

import type { TocEntry } from "@/shared/markdown";

/** What the search keeps of one page. */
export type PageIndexEntry = {
  idx: number;
  title: string;
  headings: TocEntry[];
  /** The page as it is, for the words around a match. */
  markdown: string;
  /** The same text lower-cased once, so matching is a substring test. */
  text: string;
};

export type PageSearchHit = {
  idx: number;
  title: string;
  /** What matched: the page title, a heading's text, or the words around a match in the text. */
  sub: string;
  /** The heading to scroll to, when the hit is a heading. */
  headingId?: string;
  score: number;
};

const TITLE = 3;
const HEADING = 2;
const BODY = 1;

export function indexPage(
  page: { idx: number; title: string },
  markdown: string,
  headings: TocEntry[],
): PageIndexEntry {
  return { idx: page.idx, title: page.title, headings, markdown, text: markdown.toLowerCase() };
}

const escapeRegExp = (text: string) => text.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");

/** The words around the first place `term` is in `markdown`, on one line. Where the text goes on it is cut between words
 * and ends in an ellipsis. */
export function snippetAround(markdown: string, term: string, context: number): string {
  const found = new RegExp(escapeRegExp(term), "i").exec(markdown);
  if (!found) return "";
  const matchEnd = found.index + found[0].length;
  let start = Math.max(0, found.index - context);
  let end = Math.min(markdown.length, matchEnd + context);
  if (start > 0) {
    const gap = markdown.slice(start, found.index).search(/\s/);
    if (gap >= 0) start += gap;
  }
  if (end < markdown.length) {
    const gap = markdown.slice(matchEnd, end).search(/\s\S*$/);
    if (gap >= 0) end = matchEnd + gap;
  }
  const middle = markdown.slice(start, end).replace(/\s+/g, " ").trim();
  return `${start > 0 ? "…" : ""}${middle}${end < markdown.length ? "…" : ""}`;
}

/** Every word of the query must match a title or the body; any word may match a heading. Best hits first. */
export function searchPages(
  index: readonly PageIndexEntry[],
  query: string,
  maxHits: number,
  context: number,
): PageSearchHit[] {
  const terms = query.trim().toLowerCase().split(/\s+/).filter(Boolean);
  const first = terms[0];
  if (first === undefined) return [];
  const hits: PageSearchHit[] = [];
  for (const page of index) {
    const title = page.title.toLowerCase();
    const titleHit = terms.every((term) => title.includes(term));
    const headingHits = page.headings.filter((heading) =>
      terms.some((term) => heading.text.toLowerCase().includes(term)),
    );
    const bodyHit = terms.every((term) => page.text.includes(term));

    if (titleHit) hits.push({ idx: page.idx, title: page.title, sub: page.title, score: TITLE });
    for (const heading of headingHits) {
      hits.push({
        idx: page.idx,
        title: page.title,
        sub: heading.text,
        headingId: heading.id,
        score: HEADING,
      });
    }
    if (!titleHit && headingHits.length === 0 && bodyHit) {
      hits.push({ idx: page.idx, title: page.title, sub: "", score: BODY });
    }
  }
  const best = hits.sort((a, b) => b.score - a.score).slice(0, maxHits);
  // The words around a match are cut only for the hits that are shown.
  const pages = new Map(index.map((page) => [page.idx, page]));
  return best.map((hit) => {
    if (hit.score !== BODY) return hit;
    const page = pages.get(hit.idx);
    return { ...hit, sub: (page && snippetAround(page.markdown, first, context)) || "Match in the text" };
  });
}
