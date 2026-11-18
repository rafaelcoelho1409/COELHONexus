"use client";

import { useQuery } from "@tanstack/react-query";

import {
  getDdPagesOptions,
  getDdPagesRunsActiveOptions,
  getHealthOptions,
  getYcsAdminVideosOptions,
} from "@/shared/api";

import { countActiveRuns, countDocumentationSets, countIndexedVideos, isHealthy } from "../domain";
import { PLATFORM_REFRESH_MS } from "../params";

const live = { refetchInterval: PLATFORM_REFRESH_MS, retry: false } as const;

/** Four independent reads of the real API; a failed one degrades its own row, not the panel. */
export function usePlatformStatus() {
  const api = useQuery({ ...getHealthOptions(), ...live, select: isHealthy });
  const documentationSets = useQuery({
    ...getDdPagesOptions(),
    ...live,
    select: countDocumentationSets,
  });
  const videos = useQuery({ ...getYcsAdminVideosOptions(), ...live, select: countIndexedVideos });
  const activeRuns = useQuery({ ...getDdPagesRunsActiveOptions(), ...live, select: countActiveRuns });
  return { api, documentationSets, videos, activeRuns };
}
