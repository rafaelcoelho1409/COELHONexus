"use client";

/** Ingestion stage: the live pipeline panel (when a dispatch is being tracked) above the Library. With nothing running the panel
 * gives way to one line — the Library is what this stage is for, so it is not pushed down by an empty card.
 * Search params come in as props — Next only gives them to a component inside a Suspense boundary. */

import Link from "next/link";
import type { JSX } from "react";
import { useState } from "react";

import { usePipelineTracking } from "../hooks/use-pipeline-tracking";
import { stageHref } from "../params";
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
        <p className="mb-6 text-sm text-muted-foreground">
          No pipeline is running.{" "}
          <Link href={stageHref("source")} className="text-primary-text underline underline-offset-2">
            Start one from the Source tab
          </Link>
          ; its progress will show up here.
        </p>
      )}
      <LibraryPanel refreshTick={refreshTick} />
    </div>
  );
}
