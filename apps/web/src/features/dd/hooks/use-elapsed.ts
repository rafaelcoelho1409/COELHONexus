"use client";

import { useClock } from "@/shared/lib/use-clock";

/** Milliseconds since `startedMs` while `running` (ticks once a second); null when idle. */
export function useElapsed(running: boolean, startedMs: number | null): number | null {
  const now = useClock(running);
  if (now === null) return null;
  return Math.max(0, now - (startedMs ?? now));
}
