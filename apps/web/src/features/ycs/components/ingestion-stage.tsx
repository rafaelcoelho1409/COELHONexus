"use client";

/** Ingestion stage: the live pipeline panel (when a dispatch is being tracked) above the Library. With nothing running the panel
 * gives way to one line — the Library is what this stage is for, so it is not pushed down by an empty card.
 * Search params come in as props — Next only gives them to a component inside a Suspense boundary. */

import Link from "next/link";
import type { JSX } from "react";
import { useState } from "react";

import { usePipelineTracking } from "../hooks/use-pipeline-tracking";
import { stageHref } from "../params";
import { IngestionAgenticStructure } from "./ingestion-agentic-structure";
import { LibraryPanel } from "./library-panel";
import { PipelinePanel } from "./pipeline-panel";
import type { Step } from "./pipeline-steps";
import { StatusBadge } from "./status-badge";

const IDLE_STEPS: ReadonlyArray<Step> = [
  {
    key: "playwright",
    title: "Phase 1 · Playwright",
    defaultHint: "yt-dlp metadata and Playwright transcript retrieval.",
    view: { state: "pending", pct: 0, label: "Waiting", hint: null },
  },
  {
    key: "elasticsearch",
    title: "Phase 2 · ElasticSearch",
    defaultHint: "Commit searchable video metadata and transcripts.",
    view: { state: "pending", pct: 0, label: "Waiting", hint: null },
  },
  {
    key: "qdrant",
    title: "Phase 3 · Qdrant",
    defaultHint: "Chunk transcripts and write hybrid vectors.",
    view: { state: "pending", pct: 0, label: "Waiting", hint: null },
  },
  {
    key: "neo4j",
    title: "Phase 4 · Neo4j",
    defaultHint: "DeepAgent extraction, transcript-evidence validation, and Neo4j graph writes.",
    view: { state: "pending", pct: 0, label: "Waiting", hint: null },
  },
];

export function IngestionStage(): JSX.Element {
  const { tracking, dismiss } = usePipelineTracking();
  // Library refresh ticks: one per phase reaching SUCCESS, then a final sweep when the chain ends.
  const [refreshTick, setRefreshTick] = useState(0);
  const bump = () => setRefreshTick((tick) => tick + 1);

  return (
    <div className="grid min-w-0 grid-cols-1 items-start gap-6 xl:grid-cols-[minmax(0,35fr)_minmax(0,65fr)]">
      <div className="min-w-0">
        {tracking ? (
          // A new dispatch or retry starts with fresh panel controls and node selection.
          <PipelinePanel
            key={tracking.extract}
            tracking={tracking}
            onPhaseDone={bump}
            onAllDone={bump}
            onDismiss={dismiss}
          />
        ) : (
          <section aria-label="Agentic AI ingestion structure" className="flex min-w-0 flex-col gap-3">
            <header className="flex flex-wrap items-center justify-between gap-2">
              <h2 className="text-base font-medium">Agentic AI</h2>
              <StatusBadge tone="queued" label="Waiting for a run" />
            </header>
            <p className="text-sm text-muted-foreground">
              No pipeline is running. Start one from the{" "}
              <Link href={stageHref("source")} className="text-primary-text underline underline-offset-2">
                Source tab
              </Link>
              ; its progress will show here.
            </p>
            <IngestionAgenticStructure
              steps={IDLE_STEPS}
              caption="Target flow: Playwright transcript retrieval → Elasticsearch → Qdrant and Neo4j."
            />
          </section>
        )}
      </div>
      <div className="min-w-0">
        <LibraryPanel refreshTick={refreshTick} />
      </div>
    </div>
  );
}
