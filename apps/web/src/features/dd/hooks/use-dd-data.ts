"use client";

/** Docs Distiller ingestion — TanStack Query wrappers over the generated client. The generated types for
 * these endpoints are open objects, so every payload is parsed with its zod schema (a failed parse is the
 * query's error state). The `select` functions sit at module level so their results stay referentially stable. */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  deleteDdIngestionSlug,
  getDdIngestionOptions,
  getDdIngestionQueryKey,
  getDdIngestionSlugManifestOptions,
  getDdIngestionSlugManifestQueryKey,
  getDdIngestionSlugPagesIdxOptions,
  getDdIngestionSlugPagesIdxQueryKey,
  getDdResolverOptions,
  getDdResolverQueryKey,
  getDdRunsActiveOptions,
  getDdRunsActiveQueryKey,
  getDdRunsRunIdOptions,
  getDdRunsRunIdQueryKey,
  postDdRuns,
  postDdRunsRunIdCancel,
} from "@/shared/api";

import { isRunTerminal } from "../domain/ingestion";
import {
  activeRunsSchema,
  libraryListSchema,
  manifestSchema,
  pageBodySchema,
  resolverCatalogSchema,
  runStatusSchema,
  startRunResponseSchema,
} from "../entities";
import { ACTIVE_RUN_REFRESH_MS, RUN_POLL_MS } from "../params";

const parseCatalog = (raw: unknown) => resolverCatalogSchema.parse(raw);
const parseLibrary = (raw: unknown) => libraryListSchema.parse(raw);
const parseActiveRuns = (raw: unknown) => activeRunsSchema.parse(raw);
const parseRunStatus = (raw: unknown) => runStatusSchema.parse(raw);
const parseManifest = (raw: unknown) => manifestSchema.parse(raw);
const parsePageBody = (raw: unknown) => pageBodySchema.parse(raw).body;

/** GET /dd/resolver — catalog tiles. */
export function useCatalog() {
  return useQuery({ ...getDdResolverOptions(), retry: false, select: parseCatalog });
}

/** GET /dd/ingestion — the ingested library with display names. */
export function useLibrary() {
  return useQuery({ ...getDdIngestionOptions(), retry: false, select: parseLibrary });
}

export type StartRunOutcome = { kind: "cached" | "queued" | "locked"; runId?: string; message?: string };

/** POST /dd/runs — start (or refresh) an ingestion. */
export function useStartRun() {
  const queryClient = useQueryClient();
  return useMutation<StartRunOutcome, unknown, { slug: string; refresh?: boolean }>({
    mutationFn: async (body) => {
      const result = await postDdRuns({ body, throwOnError: true });
      const data = startRunResponseSchema.parse(result.data);
      if (data.status === "cached") return { kind: "cached" };
      if (data.status === "queued") return { kind: "queued", runId: data.run_id };
      return { kind: "locked", message: data.message };
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: getDdRunsActiveQueryKey() });
      void queryClient.invalidateQueries({ queryKey: getDdIngestionQueryKey() });
    },
  });
}

/** POST /dd/runs/{run_id}/cancel — cooperative cancel. */
export function useCancelRun() {
  const queryClient = useQueryClient();
  return useMutation<string, unknown, string>({
    mutationFn: async (runId) => {
      await postDdRunsRunIdCancel({ path: { run_id: runId }, throwOnError: true });
      return runId;
    },
    onSuccess: (runId) => {
      void queryClient.invalidateQueries({ queryKey: getDdRunsRunIdQueryKey({ path: { run_id: runId } }) });
    },
  });
}

/** DELETE /dd/ingestion/{slug} — full wipe (the backend cascades to planner/synth/study state; the UI only
 * refreshes what it reads). Resolves to the slug that was removed. */
export function useDeleteLibrary() {
  const queryClient = useQueryClient();
  return useMutation<string, unknown, string>({
    mutationFn: async (slug) => {
      await deleteDdIngestionSlug({ path: { slug }, throwOnError: true });
      return slug;
    },
    onSuccess: (slug) => {
      queryClient.removeQueries({ queryKey: getDdIngestionSlugManifestQueryKey({ path: { slug } }) });
      queryClient.removeQueries({ queryKey: getDdIngestionSlugPagesIdxQueryKey({ path: { slug, idx: 0 } }) });
      void queryClient.invalidateQueries({ queryKey: getDdIngestionQueryKey() });
      void queryClient.invalidateQueries({ queryKey: getDdResolverQueryKey() });
      void queryClient.invalidateQueries({ queryKey: getDdRunsActiveQueryKey() });
    },
  });
}

/** GET /dd/runs/active — resumes an in-flight run after a reload. */
export function useActiveRun(enabled: boolean) {
  const query = useQuery({
    ...getDdRunsActiveOptions(),
    retry: false,
    refetchInterval: ACTIVE_RUN_REFRESH_MS,
    refetchIntervalInBackground: false,
    enabled,
    select: parseActiveRuns,
  });
  const runs = query.data?.active ?? [];
  // `run` is the first one (enough to know "something is running"); `runs` lets a page find its own framework's.
  return { run: runs[0], runs };
}

/** GET /dd/runs/{run_id} — the poll stops at a terminal state. */
export function useRunPoll(runId: string | null) {
  const query = useQuery({
    ...getDdRunsRunIdOptions({ path: { run_id: runId ?? "" } }),
    retry: 0,
    enabled: runId !== null,
    select: parseRunStatus,
    refetchInterval: (current) => {
      const status = runStatusSchema.safeParse(current.state.data);
      return status.success && isRunTerminal(status.data.progress?.status) ? false : RUN_POLL_MS;
    },
  });
  return { data: query.data ?? null, isPending: query.isPending, isError: query.isError };
}

/** GET /dd/ingestion/{slug}/manifest. */
export function useManifest(slug: string | null) {
  const query = useQuery({
    ...getDdIngestionSlugManifestOptions({ path: { slug: slug ?? "" } }),
    retry: false,
    enabled: slug !== null,
    select: parseManifest,
  });
  return { data: query.data ?? null, isPending: query.isPending, isError: query.isError };
}

/** GET /dd/ingestion/{slug}/pages/{idx} — the explorer's page preview. */
export function usePageBody(slug: string | null, idx: number | null) {
  const query = useQuery({
    ...getDdIngestionSlugPagesIdxOptions({ path: { slug: slug ?? "", idx: idx ?? 0 } }),
    retry: false,
    enabled: slug !== null && idx !== null,
    select: parsePageBody,
  });
  return { data: query.data ?? null, isPending: query.isPending, isError: query.isError };
}
