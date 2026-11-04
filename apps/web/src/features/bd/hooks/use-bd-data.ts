"use client";

/** Book Distiller ingestion — TanStack Query wrappers over the generated client. The responses are typed in the spec,
 * so they are used as typed; the stream and the upload (`../api.ts`) are parsed with zod. */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  client,
  deleteBdIngestionBookId,
  getBdIngestionActiveQueryKey,
  getBdIngestionBookIdOptions,
  getBdIngestionBookIdPagesPageNoOptions,
  getBdIngestionBookIdQueryKey,
  getBdIngestionOptions,
  getBdIngestionQueryKey,
  postBdIngestionBookIdCancel,
} from "@/shared/api";

import { resumeAgenticRun, startAgenticRun, uploadBook } from "../api";
import { isLive, libraryHasLiveBook, toBookStatus } from "../domain/books";
import {
  agenticRunStatusSchema,
  type BookSnapshot,
  pageBodySchema,
  type StartResponse,
  startResponseSchema,
} from "../entities";
import { LIBRARY_REFRESH_MS, SNAPSHOT_REFRESH_MS } from "../params";

/** GET /bd/ingestion — the library, re-read while any book is being extracted. */
export function useLibrary() {
  return useQuery({
    ...getBdIngestionOptions(),
    retry: false,
    refetchInterval: (query) => (libraryHasLiveBook(query.state.data) ? LIBRARY_REFRESH_MS : false),
  });
}

/** GET /bd/ingestion/{id} — the manifest and the run's progress. Polled slowly while the run is live, as a
 * fallback for a stream that cannot connect; the stream itself carries the detail. */
export function useSnapshot(bookId: string) {
  return useQuery({
    ...getBdIngestionBookIdOptions({ path: { book_id: bookId } }),
    retry: false,
    select: (data): BookSnapshot => ({
      ...data,
      agentic: zAgenticStatus(data),
    }),
    refetchInterval: (query) => {
      const data = query.state.data;
      const agentic = data ? zAgenticStatus(data) : null;
      const live = data
        ? isLive(toBookStatus(data.manifest.status)) ||
          isLive(toBookStatus(data.progress?.status)) ||
          agentic?.status === "queued" ||
          agentic?.status === "running"
        : false;
      return live ? SNAPSHOT_REFRESH_MS : false;
    },
  });
}

/** GET /bd/ingestion/{id}/pages/{n} — the whole markdown of one reader unit (a streamed unit may be cut; a unit loaded
 * from the manifest has no text at all). */
export function usePageBody(bookId: string, page: number | null, enabled: boolean) {
  return useQuery({
    ...getBdIngestionBookIdPagesPageNoOptions({ path: { book_id: bookId, page_no: page ?? 0 } }),
    enabled: enabled && page !== null,
    retry: false,
    staleTime: Number.POSITIVE_INFINITY, // an extracted page does not change until the book is extracted again
    select: (data) => pageBodySchema.parse(data),
  });
}

/** Everything a run can change: the library row, this book's snapshot, the active-runs list. */
export function useRefreshBook() {
  const queryClient = useQueryClient();
  return (bookId: string) => {
    const keepGoing = { cancelRefetch: false };
    void queryClient.invalidateQueries({ queryKey: getBdIngestionQueryKey() }, keepGoing);
    void queryClient.invalidateQueries(
      { queryKey: getBdIngestionBookIdQueryKey({ path: { book_id: bookId } }) },
      keepGoing,
    );
    void queryClient.invalidateQueries(
      {
        predicate: (query) => isBookPageQuery(query.queryKey, bookId),
      },
      keepGoing,
    );
    void queryClient.invalidateQueries({ queryKey: getBdIngestionActiveQueryKey() }, keepGoing);
  };
}

function isBookPageQuery(queryKey: readonly unknown[], bookId: string): boolean {
  const [entry] = queryKey;
  if (typeof entry !== "object" || entry === null || !("_id" in entry) || !("path" in entry)) return false;
  const path = entry.path;
  return (
    entry._id === "getBdIngestionBookIdPagesPageNo" &&
    typeof path === "object" &&
    path !== null &&
    "book_id" in path &&
    path.book_id === bookId
  );
}

function zAgenticStatus(data: unknown) {
  const extension = agenticRunStatusSchema
    .nullable()
    .optional()
    .safeParse(data && typeof data === "object" && "agentic" in data ? data.agentic : null);
  return extension.success ? (extension.data ?? null) : null;
}

/** POST /bd/ingestion — upload a PDF. */
export function useUploadBook() {
  const refresh = useRefreshBook();
  return useMutation<
    StartResponse,
    unknown,
    { file: File; autoChain?: boolean; onProgress?: (fraction: number) => void }
  >({
    mutationFn: ({ file, autoChain, onProgress }) =>
      uploadBook(file, { autoChain, ...(onProgress ? { onProgress } : {}) }),
    onSuccess: (result) => refresh(result.book_id),
  });
}

/** POST /bd/ingestion/{id}/run — start or continue a stored book (`refresh`: extract it again). */
export function useRunBook() {
  const refresh = useRefreshBook();
  return useMutation<StartResponse, unknown, { bookId: string; refresh?: boolean; autoChain?: boolean }>({
    mutationFn: async ({ bookId, refresh: again, autoChain }) => {
      const result = await client.request({
        method: "POST",
        url: "/api/v1/bd/ingestion/{book_id}/run",
        path: { book_id: bookId },
        query: { ...(again ? { refresh: true } : {}), ...(autoChain ? { auto_chain: true } : {}) },
        throwOnError: true,
      });
      return startResponseSchema.parse(result.data);
    },
    onSuccess: (_result, { bookId }) => refresh(bookId),
  });
}

export function useStartAgenticRun() {
  const refresh = useRefreshBook();
  return useMutation({
    mutationFn: startAgenticRun,
    onSuccess: (_result, bookId) => refresh(bookId),
    onError: (_error, bookId) => refresh(bookId),
  });
}

export function useResumeAgenticRun() {
  const refresh = useRefreshBook();
  return useMutation({
    mutationFn: ({ bookId, agenticRunId }: { bookId: string; agenticRunId: string }) =>
      resumeAgenticRun(bookId, agenticRunId),
    onSuccess: (_result, { bookId }) => refresh(bookId),
    onError: (_error, { bookId }) => refresh(bookId),
  });
}

/** POST /bd/ingestion/{id}/cancel — lands between slices, so the run can take a while to stop. */
export function useCancelBook() {
  return useMutation<unknown, unknown, string>({
    mutationFn: async (bookId) => {
      const result = await postBdIngestionBookIdCancel({ path: { book_id: bookId }, throwOnError: true });
      return result.data;
    },
  });
}

/** DELETE /bd/ingestion/{id} — refused (`deleted: false`) while a run holds the book. */
export function useDeleteBook() {
  const queryClient = useQueryClient();
  return useMutation<{ deleted: boolean; reason: string | null }, unknown, string>({
    mutationFn: async (bookId) => {
      const result = await deleteBdIngestionBookId({ path: { book_id: bookId }, throwOnError: true });
      return { deleted: result.data.deleted, reason: result.data.reason ?? null };
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: getBdIngestionQueryKey() });
      void queryClient.invalidateQueries({ queryKey: getBdIngestionActiveQueryKey() });
    },
  });
}
