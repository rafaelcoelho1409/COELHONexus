"use client";

/** Dispatch a picked source into the 3-phase pipeline and hand off to the Ingestion stage: the ordered
 * video ids are pre-seeded into localStorage (the same key the legacy console used) so the pipeline
 * panel can render rows immediately, then the page moves to `/ycs/ingestion?extract=…`. */

import { useRouter } from "next/navigation";
import { useState } from "react";

import { apiErrorMessage } from "@/shared/lib/api-error";

import type { PipelineQueued } from "../api";
import { ingestionHref, PIPELINE_ACTIVE_STORAGE_KEY } from "../params";

export function useDispatchPipeline(): {
  dispatch: (queue: () => Promise<PipelineQueued | null>) => Promise<boolean>;
  pending: boolean;
  error: string | null;
} {
  const router = useRouter();
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const dispatch = async (queue: () => Promise<PipelineQueued | null>): Promise<boolean> => {
    setPending(true);
    setError(null);
    try {
      const queued = await queue();
      if (!queued?.phases) {
        setError("Backend returned no phase IDs.");
        return false;
      }
      const { extract, qdrant, neo4j } = queued.phases;
      try {
        window.localStorage.setItem(
          PIPELINE_ACTIVE_STORAGE_KEY,
          JSON.stringify({
            extract,
            qdrant,
            neo4j,
            video_ids: queued.videoIds,
            startedAt: Date.now(),
          }),
        );
      } catch {
        // storage blocked: the ingestion stage still finds the run through the phase ids
      }
      router.push(ingestionHref({ extract, qdrant, neo4j }));
      return true;
    } catch (e) {
      setError(apiErrorMessage(e));
      return false;
    } finally {
      setPending(false);
    }
  };

  return { dispatch, pending, error };
}
