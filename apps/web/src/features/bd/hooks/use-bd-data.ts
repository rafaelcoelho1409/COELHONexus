"use client";

/** Book Distiller ingestion — TanStack Query wrappers over the generated client. The responses are typed in the spec,
 * so they are used as typed; the stream and the upload (`../api.ts`) are parsed with zod. */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  client,
  deleteBdIngestionBookId,
  getBdIngestionActiveQueryKey,
  getBdIngestionBookIdAgenticRunsAgenticRunIdContentSelectionOptions,
  getBdIngestionBookIdOptions,
  getBdIngestionBookIdPagesPageNoOptions,
  getBdIngestionBookIdPagesPageNoQualityOptions,
  getBdIngestionBookIdQueryKey,
  getBdIngestionOptions,
  getBdIngestionQueryKey,
  postBdIngestionBookIdCancel,
} from "@/shared/api";

import {
  cancelAgenticRun,
  fetchAgenticGraph,
  fetchProcessedBook,
  resumeAgenticRun,
  startAgenticRun,
  uploadBook,
  wipeAgenticRun,
  wipeExtraction,
} from "../api";
import { isLive, libraryHasLiveBook, toBookStatus } from "../domain/books";
import {
  type AgenticRunCancelResponse,
  type AgenticRunWipeResponse,
  agenticRunStatusSchema,
  type BookSnapshot,
  contentSelectionSchema,
  type ExtractionWipeResponse,
  type ProcessedBook,
  pageBodySchema,
  pageQualitySchema,
  type StartResponse,
  startResponseSchema,
} from "../entities";
import { LIBRARY_REFRESH_MS, SNAPSHOT_REFRESH_MS } from "../params";

/** GET /bd/ingestion/agentic-graph — Mermaid source from the compiled LangGraph. */
export function useAgenticGraph(enabled: boolean) {
  return useQuery({
    queryKey: ["bd", "ingestion", "agentic-graph"],
    queryFn: fetchAgenticGraph,
    enabled,
    retry: false,
    staleTime: Number.POSITIVE_INFINITY,
  });
}

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
export function usePageBody(
  bookId: string,
  page: number | null,
  enabled: boolean,
  /** Pin one side: the raw Docling page, or the AI-processed one. Without it the server shows the AI page when it has one. */
  source?: "docling" | "agentic",
) {
  return useQuery({
    ...getBdIngestionBookIdPagesPageNoOptions({
      path: { book_id: bookId, page_no: page ?? 0 },
      ...(source ? { query: { source } } : {}),
    }),
    enabled: enabled && page !== null,
    retry: false,
    staleTime: Number.POSITIVE_INFINITY, // an extracted page does not change until the book is extracted again
    select: (data) => pageBodySchema.parse(data),
  });
}

/** GET /bd/ingestion/{id}/pages/{n}/quality — what the current Agentic AI run found on one page and did about it. */
export function usePageQuality(bookId: string, page: number | null, enabled: boolean) {
  return useQuery({
    ...getBdIngestionBookIdPagesPageNoQualityOptions({ path: { book_id: bookId, page_no: page ?? 0 } }),
    enabled: enabled && page !== null,
    retry: false,
    select: (data) => pageQualitySchema.parse(data),
  });
}

/** The latest completed Agentic Ingestion page-selection revision, used to explain the Pages stage's Distill view. */
export function useContentSelection(bookId: string, agenticRunId: string | null) {
  return useQuery({
    ...getBdIngestionBookIdAgenticRunsAgenticRunIdContentSelectionOptions({
      path: { book_id: bookId, agentic_run_id: agenticRunId ?? "" },
    }),
    enabled: agenticRunId !== null,
    retry: false,
    select: (data) => contentSelectionSchema.parse(data),
  });
}

/** The compact page numbering and corrected contents for the completed processed book. */
export function useProcessedBook(bookId: string, agenticRunId: string | null, enabled = true) {
  return useQuery<ProcessedBook>({
    queryKey: ["bd", "processed-book", bookId, agenticRunId],
    queryFn: () => fetchProcessedBook(bookId, agenticRunId ?? ""),
    enabled: enabled && agenticRunId !== null,
    retry: false,
    staleTime: Number.POSITIVE_INFINITY,
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
    void queryClient.invalidateQueries(
      {
        predicate: (query) =>
          query.queryKey[0] === "bd" &&
          query.queryKey[1] === "processed-book" &&
          query.queryKey[2] === bookId,
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
    (entry._id === "getBdIngestionBookIdPagesPageNo" ||
      entry._id === "getBdIngestionBookIdPagesPageNoQuality") &&
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

export function useCancelAgenticRun() {
  const refresh = useRefreshBook();
  return useMutation<AgenticRunCancelResponse, unknown, { bookId: string; agenticRunId: string }>({
    mutationFn: ({ bookId, agenticRunId }) => cancelAgenticRun(bookId, agenticRunId),
    onSuccess: (_result, { bookId }) => refresh(bookId),
    onError: (_error, { bookId }) => refresh(bookId),
  });
}

export function useWipeAgenticRun() {
  const refresh = useRefreshBook();
  return useMutation<AgenticRunWipeResponse, unknown, { bookId: string; agenticRunId: string }>({
    mutationFn: ({ bookId, agenticRunId }) => wipeAgenticRun(bookId, agenticRunId),
    onSuccess: (_result, { bookId }) => refresh(bookId),
    onError: (_error, { bookId }) => refresh(bookId),
  });
}

export function useWipeExtraction() {
  const refresh = useRefreshBook();
  return useMutation<ExtractionWipeResponse, unknown, string>({
    mutationFn: wipeExtraction,
    onSuccess: (_result, bookId) => refresh(bookId),
    onError: (_error, bookId) => refresh(bookId),
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
