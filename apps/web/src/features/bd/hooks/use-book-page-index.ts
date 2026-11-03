"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";

import { getBdIngestionBookIdPagesPageNoOptions } from "@/shared/api";
import { markdownToc } from "@/shared/markdown";
import { type BookPageIndexEntry, indexBookPage } from "../domain/book-search";
import type { PageRow } from "../domain/pages";
import { pageBodySchema } from "../entities";
import { BOOK_SEARCH_BATCH_SIZE, BOOK_SEARCH_STALE_MS } from "../params";

/** Full page text is indexed only when search opens; cached page bodies are reused by the reader. */
export function useBookPageIndex(
  bookId: string,
  rows: readonly PageRow[],
  enabled: boolean,
): { entries: BookPageIndexEntry[]; loading: boolean; failed: number } {
  const queryClient = useQueryClient();
  const pageNumbers = rows.map((row) => row.page);
  const index = useQuery({
    queryKey: ["bd-book-page-index", bookId, pageNumbers],
    enabled: enabled && rows.length > 0,
    retry: false,
    staleTime: BOOK_SEARCH_STALE_MS,
    queryFn: async ({ signal }) => {
      const entries: BookPageIndexEntry[] = [];
      let failed = 0;
      for (let offset = 0; offset < rows.length; offset += BOOK_SEARCH_BATCH_SIZE) {
        if (signal.aborted) throw new DOMException("The search was cancelled.", "AbortError");
        const batch = rows.slice(offset, offset + BOOK_SEARCH_BATCH_SIZE);
        const indexed = await Promise.all(
          batch.map(async (row) => {
            const options = getBdIngestionBookIdPagesPageNoOptions({
              path: { book_id: bookId, page_no: row.page },
            });
            try {
              const response = await queryClient.fetchQuery({
                ...options,
                retry: false,
                staleTime: Infinity,
              });
              const body = pageBodySchema.parse(response);
              return indexBookPage(row, body.markdown, markdownToc(body.markdown));
            } catch {
              failed += 1;
              return null;
            }
          }),
        );
        entries.push(...indexed.filter((entry): entry is BookPageIndexEntry => entry !== null));
      }
      return { entries, failed };
    },
  });

  return {
    entries: index.data?.entries ?? [],
    loading: enabled && index.isPending,
    failed: index.data?.failed ?? 0,
  };
}
