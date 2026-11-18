"use client";

/** DD Pages extraction — TanStack Query wrappers over the generated client. The generated types for
 * these endpoints are open objects, so every payload is parsed with its zod schema (a failed parse is the
 * query's error state). The `select` functions sit at module level so their results stay referentially stable. */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  deleteDdPagesSlug,
  getDdCatalogOptions,
  getDdCatalogQueryKey,
  getDdPagesOptions,
  getDdPagesQueryKey,
  getDdPagesRunsActiveOptions,
  getDdPagesRunsActiveQueryKey,
  getDdPagesRunsRunIdOptions,
  getDdPagesRunsRunIdQueryKey,
  getDdPagesSlugManifestOptions,
  getDdPagesSlugManifestQueryKey,
  getDdPagesSlugPagesIdxOptions,
  getDdPagesSlugPagesIdxQueryKey,
  postDdPagesRuns,
  postDdPagesRunsRunIdCancel,
} from "@/shared/api";

import { fetchExtractionUsage, fetchRunPageBody } from "../api";
import { isRunTerminal } from "../domain/pages";
import {
  activeRunsSchema,
  libraryListSchema,
  manifestSchema,
  type PageVersion,
  pageBodySchema,
  resolverCatalogSchema,
  runStatusSchema,
  startRunResponseSchema,
  usagePayloadSchema,
} from "../entities";
import { ACTIVE_RUN_REFRESH_MS, RUN_POLL_MS, USAGE_REFRESH_MS } from "../params";

const parseCatalog = (raw: unknown) => resolverCatalogSchema.parse(raw);
const parseLibrary = (raw: unknown) => libraryListSchema.parse(raw);
const parseActiveRuns = (raw: unknown) => activeRunsSchema.parse(raw);
const parseRunStatus = (raw: unknown) => runStatusSchema.parse(raw);
const parseManifest = (raw: unknown) => manifestSchema.parse(raw);
const parsePageBody = (raw: unknown) => pageBodySchema.parse(raw).body;

/** GET /dd/catalog — catalog tiles. */
export function useCatalog() {
  return useQuery({ ...getDdCatalogOptions(), retry: false, select: parseCatalog });
}

/** GET /dd/pages — the saved pages library with display names. */
export function useLibrary() {
  return useQuery({ ...getDdPagesOptions(), retry: false, select: parseLibrary });
}

export type StartRunOutcome = { kind: "cached" | "queued" | "locked"; runId?: string; message?: string };

/** POST /dd/pages/runs — start or refresh documentation extraction. */
export function useStartRun() {
  const queryClient = useQueryClient();
  return useMutation<StartRunOutcome, unknown, { slug: string; refresh?: boolean }>({
    mutationFn: async (body) => {
      const result = await postDdPagesRuns({ body, throwOnError: true });
      const data = startRunResponseSchema.parse(result.data);
      if (data.status === "cached") return { kind: "cached" };
      if (data.status === "queued") return { kind: "queued", runId: data.run_id };
      return { kind: "locked", message: data.message };
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: getDdPagesRunsActiveQueryKey() });
      void queryClient.invalidateQueries({ queryKey: getDdPagesQueryKey() });
    },
  });
}

/** POST /dd/pages/runs/{run_id}/cancel — cooperative cancel. */
export function useCancelRun() {
  const queryClient = useQueryClient();
  return useMutation<string, unknown, string>({
    mutationFn: async (runId) => {
      await postDdPagesRunsRunIdCancel({ path: { run_id: runId }, throwOnError: true });
      return runId;
    },
    onSuccess: (runId) => {
      void queryClient.invalidateQueries({
        queryKey: getDdPagesRunsRunIdQueryKey({ path: { run_id: runId } }),
      });
    },
  });
}

/** DELETE /dd/pages/{slug} — full wipe (the backend cascades to planner/synth/study state; the UI only
 * refreshes what it reads). Resolves to the slug that was removed. */
export function useDeleteLibrary() {
  const queryClient = useQueryClient();
  return useMutation<string, unknown, string>({
    mutationFn: async (slug) => {
      await deleteDdPagesSlug({ path: { slug }, throwOnError: true });
      return slug;
    },
    onSuccess: (slug) => {
      queryClient.removeQueries({ queryKey: getDdPagesSlugManifestQueryKey({ path: { slug } }) });
      queryClient.removeQueries({ queryKey: getDdPagesSlugPagesIdxQueryKey({ path: { slug, idx: 0 } }) });
      void queryClient.invalidateQueries({ queryKey: getDdPagesQueryKey() });
      void queryClient.invalidateQueries({ queryKey: getDdCatalogQueryKey() });
      void queryClient.invalidateQueries({ queryKey: getDdPagesRunsActiveQueryKey() });
    },
  });
}

/** GET /dd/pages/runs/active — resumes an in-flight run after a reload. */
export function useActiveRun(enabled: boolean) {
  const query = useQuery({
    ...getDdPagesRunsActiveOptions(),
    retry: false,
    refetchInterval: ACTIVE_RUN_REFRESH_MS,
    refetchIntervalInBackground: false,
    enabled,
    select: parseActiveRuns,
  });
  const runs = query.data?.active ?? [];
  // `run` is the first one (enough to know "something is running"); `runs` lets a page find its own framework's.
  return { run: runs[0], runs, isPending: query.isPending };
}

/** GET /dd/pages/runs/{run_id} — the poll stops at a terminal state. `live` says the run is in flight (the address names it): only then is it
 * asked about again, even while the server does not know it yet (a worker that has not picked the run up has not written its status). The status of a
 * finished run expires from the server, so for it the answer, found or gone, is read once.
 *
 * `isError` stays true from the first failed answer until the run is found. A query with no data goes back to "pending" while it is refetched, which
 * made `isError` (and with it the whole screen) flip once per poll. */
export function useRunPoll(runId: string | null, live: boolean) {
  const query = useQuery({
    ...getDdPagesRunsRunIdOptions({ path: { run_id: runId ?? "" } }),
    retry: 0,
    enabled: runId !== null,
    select: parseRunStatus,
    refetchInterval: (current) => {
      if (!live) return false;
      const status = runStatusSchema.safeParse(current.state.data);
      return status.success && isRunTerminal(status.data.progress?.status) ? false : RUN_POLL_MS;
    },
  });
  return {
    data: query.data ?? null,
    isPending: query.isPending,
    isError: query.isError || (query.errorUpdateCount > 0 && query.data === undefined),
  };
}

/** GET /dd/pages/{slug}/manifest. */
export function useManifest(slug: string | null) {
  const query = useQuery({
    ...getDdPagesSlugManifestOptions({ path: { slug: slug ?? "" } }),
    retry: false,
    enabled: slug !== null,
    select: parseManifest,
  });
  return { data: query.data ?? null, isPending: query.isPending, isError: query.isError };
}

/** GET /dd/pages/{slug}/pages/{idx} — the explorer's page preview. */
export function usePageBody(slug: string | null, idx: number | null, version: PageVersion = "processed") {
  const query = useQuery({
    ...getDdPagesSlugPagesIdxOptions({
      path: { slug: slug ?? "", idx: idx ?? 0 },
      query: { version },
    }),
    retry: false,
    enabled: slug !== null && idx !== null,
    select: parsePageBody,
  });
  return { data: query.data ?? null, isPending: query.isPending, isError: query.isError };
}

/** GET /dd/pages/runs/{run_id}/pages/{idx} — page bodies from the live extraction manifest. */
export function useRunPageBody(
  runId: string | null,
  idx: number | null,
  pageKey: string | null,
  version: PageVersion = "processed",
) {
  const query = useQuery({
    queryKey: ["dd", "runs", runId, "pages", idx, pageKey, version],
    queryFn: () => fetchRunPageBody(runId ?? "", idx ?? 0, version),
    retry: false,
    enabled: runId !== null && idx !== null,
  });
  return { data: query.data ?? null, isPending: query.isPending, isError: query.isError };
}

/** Live while extraction runs; the endpoint switches to its MinIO snapshot after Redis expires. */
export function useExtractionUsage(runId: string | null, live: boolean, enabled = true) {
  return useQuery({
    queryKey: ["dd", "pages", "usage", runId],
    queryFn: () => fetchExtractionUsage(runId ?? ""),
    enabled: enabled && runId !== null,
    retry: false,
    refetchInterval: live ? USAGE_REFRESH_MS : false,
    select: (raw: unknown) => usagePayloadSchema.parse(raw),
  });
}
