import type { ChapterRange, OutlineEntry } from "../entities";

export type ChapterNode = {
  title: string;
  firstPage: number;
  lastPage: number;
  /** The outline entries that start inside this chapter, in book order (the chapter's own heading is left out). */
  entries: OutlineEntry[];
};

/**
 * The chapters of the book with their outline entries underneath. A PDF with an outline but no chapter ranges gets
 * one group holding the whole outline; one with neither gets nothing (the page list is then the only map).
 */
export function chapterTree(
  chapters: readonly ChapterRange[],
  outline: readonly OutlineEntry[],
): ChapterNode[] {
  if (chapters.length === 0) {
    if (outline.length === 0) return [];
    const last = outline.reduce((max, entry) => Math.max(max, entry.page), 1);
    return [{ title: "Outline", firstPage: 1, lastPage: last, entries: [...outline] }];
  }
  return chapters.map((chapter) => ({
    title: chapter.title,
    firstPage: chapter.first_page,
    lastPage: chapter.last_page,
    entries: outline.filter(
      (entry) =>
        entry.page >= chapter.first_page &&
        entry.page <= chapter.last_page &&
        !(entry.page === chapter.first_page && entry.title === chapter.title),
    ),
  }));
}

/** Index of the chapter that holds the page, or -1. */
export function activeChapterIndex(tree: readonly ChapterNode[], page: number | null): number {
  if (page === null) return -1;
  return tree.findIndex((chapter) => page >= chapter.firstPage && page <= chapter.lastPage);
}

/** Index (within the chapter's entries) of the section being read: the last entry that starts on or before the page. */
export function activeEntryIndex(chapter: ChapterNode, page: number | null): number {
  if (page === null) return -1;
  let found = -1;
  chapter.entries.forEach((entry, index) => {
    if (entry.page <= page) found = index;
  });
  return found;
}
