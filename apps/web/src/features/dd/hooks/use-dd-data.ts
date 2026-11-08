"use client";

/** Docs Distiller ingestion — TanStack Query wrappers over the generated client. The generated types for
 * these endpoints are open objects, so every payload is parsed with its zod schema (a failed parse is the
 * query's error state). The `select` functions sit at module level so their results stay referentially stable. */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  deleteDdIngestionSlug,
  getDdCatalogOptions,
  getDdCatalogQueryKey,
  getDdIngestionOptions,
  getDdIngestionQueryKey,
  getDdIngestionRunsActiveOptions,
  getDdIngestionRunsActiveQueryKey,
  getDdIngestionRunsRunIdOptions,
  getDdIngestionRunsRunIdQueryKey,
  getDdIngestionSlugManifestOptions,
  getDdIngestionSlugManifestQueryKey,
  getDdIngestionSlugPagesIdxOptions,
  getDdIngestionSlugPagesIdxQuality,
  getDdIngestionSlugPagesIdxQualityQueryKey,
  getDdIngestionSlugPagesIdxQueryKey,
  postDdIngestionRuns,
  postDdIngestionRunsRunIdCancel,
} from "@/shared/api";

import {
  fetchIngestionUsage,
  fetchRunPageBody,
  resumeAgenticRun,
  startAgenticRun,
  wipeAgenticRun,
} from "../api";
import { isRunTerminal } from "../domain/ingestion";
import {
  type AgenticWipeResponse,
  activeRunsSchema,
  libraryListSchema,
  manifestSchema,
  type PageVersion,
  pageBodySchema,
  pageQualitySchema,
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

/** GET /dd/ingestion — the ingested library with display names. */
export function useLibrary() {
  return useQuery({ ...getDdIngestionOptions(), retry: false, select: parseLibrary });
}

export type StartRunOutcome = { kind: "cached" | "queued" | "locked"; runId?: string; message?: string };

/** POST /dd/ingestion/runs — start (or refresh) an ingestion. */
export function useStartRun() {
  const queryClient = useQueryClient();
  return useMutation<StartRunOutcome, unknown, { slug: string; refresh?: boolean }>({
    mutationFn: async (body) => {
      const result = await postDdIngestionRuns({ body, throwOnError: true });
      const data = startRunResponseSchema.parse(result.data);
      if (data.status === "cached") return { kind: "cached" };
      if (data.status === "queued") return { kind: "queued", runId: data.run_id };
      return { kind: "locked", message: data.message };
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: getDdIngestionRunsActiveQueryKey() });
      void queryClient.invalidateQueries({ queryKey: getDdIngestionQueryKey() });
    },
  });
}

function startOutcome(data: ReturnType<typeof startRunResponseSchema.parse>): StartRunOutcome {
  if (data.status === "queued") return { kind: "queued", runId: data.run_id };
  return { kind: "locked", message: data.message };
}

/** POST /dd/ingestion/{slug}/agentic/runs — start AI from the saved extracted pages. */
export function useStartAgenticRun() {
  const queryClient = useQueryClient();
  return useMutation<StartRunOutcome, unknown, string>({
    mutationFn: async (slug) => startOutcome(await startAgenticRun(slug)),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: getDdIngestionRunsActiveQueryKey() });
      void queryClient.invalidateQueries({ queryKey: getDdIngestionQueryKey() });
    },
  });
}

/** POST /dd/ingestion/{slug}/agentic/runs/{run_id}/resume. */
export function useResumeAgenticRun() {
  const queryClient = useQueryClient();
  return useMutation<StartRunOutcome, unknown, { slug: string; runId: string }>({
    mutationFn: async ({ slug, runId }) => startOutcome(await resumeAgenticRun(slug, runId)),
    onSuccess: (_outcome, { runId }) => {
      void queryClient.invalidateQueries({ queryKey: getDdIngestionRunsActiveQueryKey() });
      void queryClient.invalidateQueries({
        queryKey: getDdIngestionRunsRunIdQueryKey({ path: { run_id: runId } }),
      });
    },
  });
}

/** DELETE /dd/ingestion/{slug}/agentic/runs/{run_id} — remove AI-only artifacts. */
export function useWipeAgenticRun() {
  const queryClient = useQueryClient();
  return useMutation<AgenticWipeResponse, unknown, { slug: string; runId: string }>({
    mutationFn: ({ slug, runId }) => wipeAgenticRun(slug, runId),
    onSuccess: (result) => {
      void queryClient.invalidateQueries({
        queryKey: getDdIngestionRunsRunIdQueryKey({ path: { run_id: result.run_id } }),
      });
      void queryClient.invalidateQueries(getDdIngestionSlugManifestOptions({ path: { slug: result.slug } }));
      void queryClient.invalidateQueries({ queryKey: getDdIngestionQueryKey() });
      void queryClient.invalidateQueries({ queryKey: ["dd", "ingestion", "usage", result.run_id] });
    },
  });
}

/** POST /dd/ingestion/runs/{run_id}/cancel — cooperative cancel. */
export function useCancelRun() {
  const queryClient = useQueryClient();
  return useMutation<string, unknown, string>({
    mutationFn: async (runId) => {
      await postDdIngestionRunsRunIdCancel({ path: { run_id: runId }, throwOnError: true });
      return runId;
    },
    onSuccess: (runId) => {
      void queryClient.invalidateQueries({
        queryKey: getDdIngestionRunsRunIdQueryKey({ path: { run_id: runId } }),
      });
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
      void queryClient.invalidateQueries({ queryKey: getDdCatalogQueryKey() });
      void queryClient.invalidateQueries({ queryKey: getDdIngestionRunsActiveQueryKey() });
    },
  });
}

/** GET /dd/ingestion/runs/active — resumes an in-flight run after a reload. */
export function useActiveRun(enabled: boolean) {
  const query = useQuery({
    ...getDdIngestionRunsActiveOptions(),
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

/** GET /dd/ingestion/runs/{run_id} — the poll stops at a terminal state. `live` says the run is in flight (the address names it): only then is it
 * asked about again, even while the server does not know it yet (a worker that has not picked the run up has not written its status). The status of a
 * finished run expires from the server, so for it the answer, found or gone, is read once.
 *
 * `isError` stays true from the first failed answer until the run is found. A query with no data goes back to "pending" while it is refetched, which
 * made `isError` (and with it the whole screen) flip once per poll. */
export function useRunPoll(runId: string | null, live: boolean) {
  const query = useQuery({
    ...getDdIngestionRunsRunIdOptions({ path: { run_id: runId ?? "" } }),
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
export function usePageBody(slug: string | null, idx: number | null, version: PageVersion = "processed") {
  const query = useQuery({
    ...getDdIngestionSlugPagesIdxOptions({
      path: { slug: slug ?? "", idx: idx ?? 0 },
      query: { version },
    }),
    retry: false,
    enabled: slug !== null && idx !== null,
    select: parsePageBody,
  });
  return { data: query.data ?? null, isPending: query.isPending, isError: query.isError };
}

/** GET /dd/ingestion/{slug}/pages/{idx}/quality — the finalized page's audit and repair report. */
export function usePageQuality(slug: string, idx: number, agenticRunId: string, enabled: boolean) {
  return useQuery({
    queryKey: [...getDdIngestionSlugPagesIdxQualityQueryKey({ path: { slug, idx } }), agenticRunId],
    queryFn: async () => {
      const result = await getDdIngestionSlugPagesIdxQuality({
        path: { slug, idx },
        throwOnError: true,
      });
      return pageQualitySchema.parse(result.data);
    },
    retry: false,
    enabled,
  });
}

/** GET /dd/ingestion/runs/{run_id}/pages/{idx} — page bodies from the in-flight raw-to-agentic manifest. */
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

/** Live while ingestion runs; the endpoint switches to its MinIO snapshot after Redis expires. */
export function useIngestionUsage(runId: string | null, live: boolean) {
  return useQuery({
    queryKey: ["dd", "ingestion", "usage", runId],
    queryFn: () => fetchIngestionUsage(runId ?? ""),
    enabled: runId !== null,
    retry: false,
    refetchInterval: live ? USAGE_REFRESH_MS : false,
    select: (raw: unknown) => usagePayloadSchema.parse(raw),
  });
}
