"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback } from "react";

import {
  getDdPipelineActiveQueryKey,
  getDdPipelinePlannerInfoQueryKey,
  getDdPipelineSynthInfoQueryKey,
} from "@/shared/api";

import { fetchIngestionGraph, fetchStageInfo } from "../api";
import { type RunStage, STAGE_INFO_STALE_MS } from "../params";

/** One definition for `useStageInfo` and for the recovery code that reads the cache imperatively. */
export function stageInfoQuery(stage: RunStage) {
  return {
    queryKey: stage === "planner" ? getDdPipelinePlannerInfoQueryKey() : getDdPipelineSynthInfoQueryKey(),
    queryFn: () => fetchStageInfo(stage),
    staleTime: STAGE_INFO_STALE_MS,
    retry: false,
  };
}

/** Which graph nodes the server implements (null until known or when the call fails). */
export function useStageInfo(stage: RunStage) {
  return useStageInfoQuery(stage).data ?? null;
}

/** Full query state for stage boards that need to distinguish loading from unavailable topology. */
export function useStageInfoQuery(stage: RunStage) {
  return useQuery(stageInfoQuery(stage));
}

/** Source for the compiled Ingestion graph, separate from the live run-status graph. */
export function useIngestionGraph() {
  const query = useQuery({
    queryKey: ["dd", "ingestion", "agentic-graph"],
    queryFn: fetchIngestionGraph,
    staleTime: STAGE_INFO_STALE_MS,
    retry: false,
  });
  return {
    source: query.data?.graph_mermaid ?? null,
    loading: query.isPending,
  };
}

/** Tells every stage view to re-read the cross-stage run registry. */
export function useInvalidatePipeline(): () => void {
  const queryClient = useQueryClient();
  return useCallback(() => {
    void queryClient.invalidateQueries({ queryKey: getDdPipelineActiveQueryKey() });
  }, [queryClient]);
}
