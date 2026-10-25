"use client";

import { useQueries, useQuery } from "@tanstack/react-query";

import {
  getDdPlannerDebugGraphThreadIdLlmCountersQueryKey,
  getDdPlannerSlugHealthQueryKey,
  getDdSynthDebugGraphThreadIdLlmCountersQueryKey,
} from "@/shared/api";

import { fetchPlannerHealth, fetchUsage } from "../api";
import type { ChapterCell } from "../domain/chapters";
import type { UsagePayload } from "../entities";
import { type RunStage, USAGE_REFRESH_MS } from "../params";

function usageQuery(stage: RunStage, threadId: string, live: boolean) {
  const path = { thread_id: threadId };
  return {
    queryKey:
      stage === "planner"
        ? getDdPlannerDebugGraphThreadIdLlmCountersQueryKey({ path })
        : getDdSynthDebugGraphThreadIdLlmCountersQueryKey({ path }),
    queryFn: () => fetchUsage(stage, threadId),
    // While a run is live the counters move; afterwards they are final.
    refetchInterval: live ? USAGE_REFRESH_MS : (false as const),
    retry: false,
  };
}

/** LLM usage of one run thread; null until it loads, or when the counters cannot be read. */
export function useRunUsage(
  stage: RunStage,
  threadId: string | null,
  { live, enabled = true }: { live: boolean; enabled?: boolean },
): UsagePayload | null {
  const query = useQuery({
    ...usageQuery(stage, threadId ?? "", live),
    enabled: enabled && threadId !== null,
  });
  return query.data ?? null;
}

export type ChapterUsage = { cell: ChapterCell; usage: UsagePayload | null };

/** LLM usage of every chapter that has a thread — rendered ones and the one running now. */
export function useChapterUsage(
  cells: readonly ChapterCell[],
  { live, enabled = true }: { live: boolean; enabled?: boolean },
): ChapterUsage[] {
  const withThread = cells.filter((cell) => cell.threadId !== null);
  const results = useQueries({
    queries: withThread.map((cell) => ({
      ...usageQuery("synth", cell.threadId ?? "", live && cell.status === "running"),
      enabled,
    })),
  });
  return withThread.map((cell, index) => ({ cell, usage: results[index]?.data ?? null }));
}

/** How the latest plan of this framework was produced. */
export function usePlannerHealth(slug: string, enabled: boolean) {
  const query = useQuery({
    queryKey: getDdPlannerSlugHealthQueryKey({ path: { slug } }),
    queryFn: () => fetchPlannerHealth(slug),
    enabled,
    retry: false,
  });
  return query.data ?? null;
}
