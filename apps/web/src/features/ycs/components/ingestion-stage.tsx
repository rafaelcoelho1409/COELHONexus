"use client";

/** Ingestion stage: the live pipeline panel (when a dispatch is being tracked) above the Library.
 * Search params come in as props — Next only gives them to a component inside a Suspense boundary. */

import type { JSX } from "react";
import { useState } from "react";
import { usePipelineTracking } from "../hooks/use-pipeline-tracking";
import { LibraryPanel } from "./library-panel";
import { PipelinePanel } from "./pipeline-panel";

export function IngestionStage(): JSX.Element {
  const { tracking, dismiss } = usePipelineTracking();
  // Library refresh ticks: one per phase reaching SUCCESS, then a final sweep when the chain ends.
  const [refreshTick, setRefreshTick] = useState(0);
  const bump = () => setRefreshTick((tick) => tick + 1);

  return (
    <div>
      {tracking ? (
        // `key`: a new dispatch (or retry) starts from a clean panel — its stop/wipe state, not the
        // last run's.
        <PipelinePanel
          key={tracking.extract}
          tracking={tracking}
          onPhaseDone={bump}
          onAllDone={bump}
          onDismiss={dismiss}
        />
      ) : (
        <section aria-label="Pipeline progress" className="mb-4 rounded-md border border-border bg-card p-3">
          <h2 className="text-base font-medium">Nothing is running</h2>
          <p className="mt-1 text-sm text-muted-foreground">
            Dispatch a pipeline from the Source tab — its progress will show up here.
          </p>
        </section>
      )}
      <LibraryPanel refreshTick={refreshTick} />
    </div>
  );
}
