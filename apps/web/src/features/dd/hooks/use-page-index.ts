"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";

import { getDdIngestionSlugPagesIdxOptions } from "@/shared/api";
import { markdownToc } from "@/shared/markdown";

import { fetchRunPageBody } from "../api";
import { pageTitle } from "../domain/materials";
import { indexPage, type PageIndexEntry } from "../domain/materials-search";
import { type ManifestEntry, pageBodySchema } from "../entities";
import { MATERIALS_SEARCH_BATCH_SIZE, MATERIALS_SEARCH_STALE_MS } from "../params";

/**
 * What the search needs from every ingested page: its title, headings and text. The pages are read only once `enabled`
 * (the reader opened the search), a few at a time, and go through the same query cache the reader uses, so a page already
 * opened is not fetched again. With a `runId` the pages are those of the run on screen, not the saved library's.
 */
export function usePageIndex(
  slug: string,
  runId: string | null,
  entries: readonly ManifestEntry[],
  enabled: boolean,
): { entries: PageIndexEntry[]; loading: boolean; failed: number } {
  const queryClient = useQueryClient();
  const index = useQuery({
    queryKey: ["dd-materials-index", slug, runId, entries.map((entry) => `${entry.idx}:${entry.key ?? ""}`)],
    enabled: enabled && entries.length > 0,
    retry: false,
    staleTime: MATERIALS_SEARCH_STALE_MS,
    queryFn: async ({ signal }) => {
      const indexed: PageIndexEntry[] = [];
      let failed = 0;
      for (let offset = 0; offset < entries.length; offset += MATERIALS_SEARCH_BATCH_SIZE) {
        if (signal.aborted) throw new DOMException("The search was cancelled.", "AbortError");
        const batch = entries.slice(offset, offset + MATERIALS_SEARCH_BATCH_SIZE);
        const pages = await Promise.all(
          batch.map(async (entry) => {
            try {
              const markdown = runId
                ? await queryClient.fetchQuery({
                    queryKey: ["dd", "runs", runId, "pages", entry.idx, entry.key ?? null],
                    queryFn: () => fetchRunPageBody(runId, entry.idx),
                    retry: false,
                    staleTime: Infinity,
                  })
                : pageBodySchema.parse(
                    await queryClient.fetchQuery({
                      ...getDdIngestionSlugPagesIdxOptions({ path: { slug, idx: entry.idx } }),
                      retry: false,
                      staleTime: Infinity,
                    }),
                  ).body;
              return indexPage({ idx: entry.idx, title: pageTitle(entry) }, markdown, markdownToc(markdown));
            } catch {
              failed += 1;
              return null;
            }
          }),
        );
        indexed.push(...pages.filter((page): page is PageIndexEntry => page !== null));
      }
      return { entries: indexed, failed };
    },
  });

  return {
    entries: index.data?.entries ?? [],
    loading: enabled && index.isPending,
    failed: index.data?.failed ?? 0,
  };
}
