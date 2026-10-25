"use client";

import { useQuery } from "@tanstack/react-query";

import { getDdPipelineActiveQueryKey } from "@/shared/api";

import { fetchPipelineActive } from "../api";
import { ACTIVE_REFRESH_MS, type RunStage } from "../params";

export type CrossStageBlocker = {
  /** A run is in flight somewhere in the app, so this stage may not start. */
  blocked: boolean;
  message: string | null;
};

/** Polls the server's run registry: planner and synth never run at the same time, anywhere in the app,
 * so every stage view disables its Start button while either is active. */
export function useCrossStageBlocker(stage: RunStage): CrossStageBlocker {
  const active = useQuery({
    queryKey: getDdPipelineActiveQueryKey(),
    queryFn: () => fetchPipelineActive(),
    refetchInterval: ACTIVE_REFRESH_MS,
    refetchOnWindowFocus: true,
  });
  const plannerSlug = active.data?.planner?.slug ?? null;
  const synthSlug = active.data?.synth?.slug ?? null;
  const otherSlug = stage === "planner" ? synthSlug : plannerSlug;
  const ownSlug = stage === "planner" ? plannerSlug : synthSlug;

  const message = otherSlug
    ? `The ${stage === "planner" ? "synth" : "planner"} stage is running for "${otherSlug}" — one run at a time.`
    : ownSlug
      ? `This stage is already running for "${ownSlug}" elsewhere — stop it there first.`
      : null;

  return { blocked: message !== null, message };
}
