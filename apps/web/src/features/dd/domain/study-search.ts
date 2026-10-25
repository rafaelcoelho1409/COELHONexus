/** Searching across every rendered chapter of a framework: a hit is a chapter title, a heading, or a match
 * somewhere in the text, in that order of relevance. */

import type { TocEntry } from "@/shared/markdown";

/** What the search keeps of one chapter. */
export type ChapterIndexEntry = {
  chapterId: string;
  title: string;
  headings: TocEntry[];
  /** The whole text, lower-cased once, so matching is a substring test. */
  text: string;
};

export type SearchHit = {
  chapterId: string;
  title: string;
  /** What matched: the chapter title, a heading's text, or a note that the match is in the body. */
  sub: string;
  /** The heading to scroll to, when the hit is a heading. */
  headingId?: string;
  score: number;
};

const MAX_HITS = 30;

export function indexChapter(
  chapter: { id: string; title: string },
  markdown: string,
  headings: TocEntry[],
): ChapterIndexEntry {
  return { chapterId: chapter.id, title: chapter.title, headings, text: markdown.toLowerCase() };
}

/** Every word of the query must match a title or the body; any word may match a heading. Best hits first. */
export function searchChapters(index: readonly ChapterIndexEntry[], query: string): SearchHit[] {
  const terms = query.trim().toLowerCase().split(/\s+/).filter(Boolean);
  if (terms.length === 0) return [];
  const hits: SearchHit[] = [];
  for (const chapter of index) {
    const title = chapter.title.toLowerCase();
    const titleHit = terms.every((term) => title.includes(term));
    const headingHits = chapter.headings.filter((heading) =>
      terms.some((term) => heading.text.toLowerCase().includes(term)),
    );
    const bodyHit = terms.every((term) => chapter.text.includes(term));

    if (titleHit)
      hits.push({ chapterId: chapter.chapterId, title: chapter.title, sub: chapter.title, score: 3 });
    for (const heading of headingHits) {
      hits.push({
        chapterId: chapter.chapterId,
        title: chapter.title,
        sub: heading.text,
        headingId: heading.id,
        score: 2,
      });
    }
    if (!titleHit && headingHits.length === 0 && bodyHit) {
      hits.push({ chapterId: chapter.chapterId, title: chapter.title, sub: "Match in the text", score: 1 });
    }
  }
  return hits.sort((a, b) => b.score - a.score).slice(0, MAX_HITS);
}
