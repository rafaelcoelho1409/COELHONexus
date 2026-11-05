import type { TocEntry } from "@/shared/markdown";

import type { PageRow } from "./pages";

export type BookPageIndexEntry = {
  page: number;
  title: string;
  headings: TocEntry[];
  text: string;
};

export type BookSearchHit = {
  page: number;
  title: string;
  sub: string;
  /** The heading to scroll to, when the hit is a heading. */
  headingId?: string;
  score: number;
};

export function indexBookPage(row: PageRow, markdown: string, headings: TocEntry[]): BookPageIndexEntry {
  return {
    page: row.page,
    title: row.sectionPath.at(-1) ?? `Page ${row.page}`,
    headings,
    text: markdown.toLowerCase(),
  };
}

export function searchBookPages(
  index: readonly BookPageIndexEntry[],
  query: string,
  maxHits: number,
): BookSearchHit[] {
  const terms = query.trim().toLowerCase().split(/\s+/).filter(Boolean);
  if (terms.length === 0) return [];

  const hits: BookSearchHit[] = [];
  for (const page of index) {
    const titleHit = terms.every((term) => page.title.toLowerCase().includes(term));
    const headingHits = page.headings.filter((heading) =>
      terms.some((term) => heading.text.toLowerCase().includes(term)),
    );
    const bodyHit = terms.every((term) => page.text.includes(term));

    if (titleHit) hits.push({ page: page.page, title: page.title, sub: page.title, score: 3 });
    for (const heading of headingHits) {
      hits.push({ page: page.page, title: page.title, sub: heading.text, headingId: heading.id, score: 2 });
    }
    if (!titleHit && headingHits.length === 0 && bodyHit) {
      hits.push({ page: page.page, title: page.title, sub: "Match in the text", score: 1 });
    }
  }
  return hits.sort((a, b) => b.score - a.score || a.page - b.page).slice(0, maxHits);
}
