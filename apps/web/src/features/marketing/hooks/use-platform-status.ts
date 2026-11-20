"use client";

import { useQuery } from "@tanstack/react-query";

import {
  getHealthOptions,
  getYcsAdminVideosOptions,
} from "@/shared/api";

import { countIndexedVideos, isHealthy } from "../domain";
import { PLATFORM_REFRESH_MS } from "../params";

const live = { refetchInterval: PLATFORM_REFRESH_MS, retry: false } as const;

/** Independent reads of the real API; a failed one degrades its own row, not the panel. */
export function usePlatformStatus() {
  const api = useQuery({ ...getHealthOptions(), ...live, select: isHealthy });
  const videos = useQuery({ ...getYcsAdminVideosOptions(), ...live, select: countIndexedVideos });
  return { api, videos };
}
