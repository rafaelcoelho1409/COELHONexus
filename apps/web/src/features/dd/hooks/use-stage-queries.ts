"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback } from "react";

import { getDdPipelineActiveQueryKey, getDdPlannerInfoQueryKey, getDdSynthInfoQueryKey } from "@/shared/api";

import { fetchStageInfo } from "../api";
import { type RunStage, STAGE_INFO_STALE_MS } from "../params";

/** One definition for `useStageInfo` and for the recovery code that reads the cache imperatively. */
export function stageInfoQuery(stage: RunStage) {
  return {
    queryKey: stage === "planner" ? getDdPlannerInfoQueryKey() : getDdSynthInfoQueryKey(),
    queryFn: () => fetchStageInfo(stage),
    staleTime: STAGE_INFO_STALE_MS,
    retry: false,
  };
}

/** Which graph nodes the server implements (null until known or when the call fails). */
export function useStageInfo(stage: RunStage) {
  return useQuery(stageInfoQuery(stage)).data ?? null;
}

/** Tells every stage view to re-read the cross-stage run registry. */
export function useInvalidatePipeline(): () => void {
  const queryClient = useQueryClient();
  return useCallback(() => {
    void queryClient.invalidateQueries({ queryKey: getDdPipelineActiveQueryKey() });
  }, [queryClient]);
}
