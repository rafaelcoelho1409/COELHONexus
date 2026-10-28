"use client";

/** Which pipeline the Ingestion stage tracks, resolved with the legacy console's precedence:
 * URL `?extract=&qdrant=&neo4j=` (a Source dispatch or a Rerun redirect) → the per-browser record the
 * previous dispatch wrote (24 h TTL, matching the backend's Redis TTL) → nothing. The record is written
 * here, not by the Source dispatcher, so the hand-off is one-way: only the stage that owns the panel
 * persists it. */

import { useRouter, useSearchParams } from "next/navigation";
import { useCallback, useEffect, useState } from "react";

import { pipelineVideoIds } from "../api";
import { PIPELINE_ACTIVE_STORAGE_KEY, PIPELINE_STORAGE_TTL_MS } from "../params";

export type PipelineTracking = {
  extract: string;
  qdrant: string;
  neo4j: string;
  videoIds: string[];
  startedAtMs: number;
};

type StoredPipeline = {
  extract: string;
  qdrant: string;
  neo4j: string;
  video_ids?: string[];
  startedAt?: number;
};

function readStored(): PipelineTracking | null {
  try {
    const raw = localStorage.getItem(PIPELINE_ACTIVE_STORAGE_KEY);
    if (!raw) return null;
    const parsed: unknown = JSON.parse(raw);
    const stored = parsed as StoredPipeline | null;
    if (!stored?.extract || !stored.qdrant || !stored.neo4j) return null;
    const startedAt = stored.startedAt ?? Date.now();
    // Expire stale records so abandoned panels don't linger forever.
    if (Date.now() - startedAt > PIPELINE_STORAGE_TTL_MS) {
      localStorage.removeItem(PIPELINE_ACTIVE_STORAGE_KEY);
      return null;
    }
    return {
      extract: stored.extract,
      qdrant: stored.qdrant,
      neo4j: stored.neo4j,
      videoIds: Array.isArray(stored.video_ids) ? stored.video_ids : [],
      startedAtMs: startedAt,
    };
  } catch {
    return null;
  }
}

function writeStored(tracking: PipelineTracking): void {
  try {
    localStorage.setItem(
      PIPELINE_ACTIVE_STORAGE_KEY,
      JSON.stringify({
        extract: tracking.extract,
        qdrant: tracking.qdrant,
        neo4j: tracking.neo4j,
        video_ids: tracking.videoIds,
        startedAt: tracking.startedAtMs,
      }),
    );
  } catch {
    // Quota or privacy mode — tracking still works for this page view.
  }
}

export function usePipelineTracking(): { tracking: PipelineTracking | null; dismiss: () => void } {
  const params = useSearchParams();
  const router = useRouter();
  const [tracking, setTracking] = useState<PipelineTracking | null>(null);

  const extract = params.get("extract");
  const qdrant = params.get("qdrant");
  const neo4j = params.get("neo4j");

  useEffect(() => {
    let cancelled = false;
    async function adopt() {
      if (extract && qdrant && neo4j) {
        // The redirect carries phase ids but not video_ids (the URL would bloat) — fetch the
        // dispatch's persisted ordering once so the per-video table is stable.
        let videoIds: string[] = [];
        try {
          videoIds = await pipelineVideoIds(extract);
        } catch {
          // Fall back to empty — the table renders from all_items only.
        }
        if (cancelled) return;
        const adopted: PipelineTracking = {
          extract,
          qdrant,
          neo4j,
          videoIds,
          startedAtMs: Date.now(),
        };
        writeStored(adopted);
        setTracking(adopted);
        return;
      }
      const stored = readStored();
      if (!stored) {
        setTracking(null);
        return;
      }
      // Records written before video_ids existed (older dispatches) rehydrate once.
      if (!stored.videoIds.length) {
        try {
          stored.videoIds = await pipelineVideoIds(stored.extract);
          writeStored(stored);
        } catch {
          // The table renders from all_items only.
        }
        if (cancelled) return;
      }
      setTracking(stored);
    }
    void adopt();
    return () => {
      cancelled = true;
    };
  }, [extract, qdrant, neo4j]);

  const dismiss = useCallback(() => {
    try {
      localStorage.removeItem(PIPELINE_ACTIVE_STORAGE_KEY);
    } catch {
      // Nothing to clean up.
    }
    setTracking(null);
    // Drop the URL ids too, or this effect would re-adopt them on the next render.
    router.replace("/ycs/ingestion", { scroll: false });
  }, [router]);

  return { tracking, dismiss };
}
