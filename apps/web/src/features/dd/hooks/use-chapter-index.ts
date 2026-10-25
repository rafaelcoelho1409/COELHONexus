"use client";

import { useQueries } from "@tanstack/react-query";

import { markdownToc } from "@/shared/markdown";

import { fetchChapterMarkdown } from "../api";
import { CHAPTER_PLUGINS, unwrapChapterFence } from "../domain/chapter-markdown";
import { type ChapterIndexEntry, indexChapter } from "../domain/study-search";

const INDEX_STALE_MS = 60_000;

/**
 * What the search needs from every rendered chapter of a framework: its title, headings and text. Chapters are
 * fetched only once `enabled` (the reader opened the search), then kept in the query cache.
 */
export function useChapterIndex(
  slug: string,
  chapters: ReadonlyArray<{ id: string; title: string; rendered: boolean }>,
  enabled: boolean,
): { entries: ChapterIndexEntry[]; loading: boolean } {
  const rendered = chapters.filter((chapter) => chapter.rendered);
  const results = useQueries({
    queries: rendered.map((chapter) => ({
      queryKey: ["dd-study-index", slug, chapter.id],
      queryFn: async () => {
        const markdown = unwrapChapterFence(await fetchChapterMarkdown(slug, chapter.id));
        return indexChapter(chapter, markdown, markdownToc(markdown, CHAPTER_PLUGINS));
      },
      enabled,
      staleTime: INDEX_STALE_MS,
      retry: false,
    })),
  });
  return {
    entries: results.flatMap((result) => (result.data ? [result.data] : [])),
    loading: enabled && results.some((result) => result.isPending),
  };
}
