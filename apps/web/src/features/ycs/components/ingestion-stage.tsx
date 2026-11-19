"use client";

/** Ingestion stage: the live pipeline panel (when a dispatch is being tracked) above the Library. With nothing running the panel
 * gives way to one line — the Library is what this stage is for, so it is not pushed down by an empty card.
 * Search params come in as props — Next only gives them to a component inside a Suspense boundary. */

import Link from "next/link";
import { useRouter } from "next/navigation";
import type { JSX } from "react";
import { useState } from "react";

import { usePipelineTracking } from "../hooks/use-pipeline-tracking";
import { stageHref } from "../params";
import { IngestionAgenticStructure } from "./ingestion-agentic-structure";
import { LibraryPanel } from "./library-panel";
import { PipelinePanel } from "./pipeline-panel";
import type { Step } from "./pipeline-steps";
import { PipelineUsageSheet } from "./pipeline-usage-sheet";
import { StatusBadge } from "./status-badge";

const IDLE_STEPS: ReadonlyArray<Step> = [
  {
    key: "playwright",
    title: "Phase 1 · Playwright",
    defaultHint: "yt-dlp metadata and Playwright transcript retrieval.",
    reads: ["Selected video ids", "Source options"],
    writes: ["Video metadata", "Transcripts"],
    view: { state: "pending", pct: 0, label: "Waiting", hint: null },
  },
  {
    key: "elasticsearch",
    title: "Phase 2 · ElasticSearch",
    defaultHint: "Commit searchable video metadata and transcripts.",
    reads: ["Video metadata", "Transcripts"],
    writes: ["Searchable metadata", "Transcript index"],
    view: { state: "pending", pct: 0, label: "Waiting", hint: null },
  },
  {
    key: "qdrant",
    title: "Phase 3 · Qdrant",
    defaultHint: "Chunk transcripts and write hybrid vectors.",
    reads: ["Transcripts"],
    writes: ["Transcript chunks", "Hybrid vectors"],
    view: { state: "pending", pct: 0, label: "Waiting", hint: null },
  },
  {
    key: "neo4j",
    title: "Phase 4 · Neo4j",
    defaultHint: "LLMGraphTransformer extraction, transcript-evidence validation, and Neo4j graph writes.",
    reads: ["Transcripts", "Video metadata"],
    writes: ["Knowledge graph", "Evidence-linked relationships"],
    view: { state: "pending", pct: 0, label: "Waiting", hint: null },
  },
];

export function IngestionStage(): JSX.Element {
  const router = useRouter();
  const { tracking } = usePipelineTracking();
  // Library refresh ticks: one per phase reaching SUCCESS, then a final sweep when the chain ends.
  const [refreshTick, setRefreshTick] = useState(0);
  const [usageOpen, setUsageOpen] = useState(false);
  const bump = () => setRefreshTick((tick) => tick + 1);

  return (
    <div className="grid min-w-0 grid-cols-1 items-start gap-6 xl:grid-cols-[minmax(0,35fr)_minmax(0,65fr)]">
      <div className="min-w-0">
        {tracking ? (
          // A new dispatch or retry starts with fresh panel controls and node selection.
          <PipelinePanel key={tracking.extract} tracking={tracking} onPhaseDone={bump} onAllDone={bump} />
        ) : (
          <section aria-label="Agentic RAG ingestion structure" className="flex min-w-0 flex-col gap-3">
            <header className="flex flex-wrap items-center justify-between gap-2">
              <h2 className="text-base font-medium">Agentic RAG</h2>
              <StatusBadge tone="queued" label="Waiting for a run" />
            </header>
            <p className="text-sm text-muted-foreground">
              No Agentic RAG run is active. Choose videos on the{" "}
              <Link href={stageHref("source")} className="text-primary-text underline underline-offset-2">
                Source tab
              </Link>
              ; its ingestion and retrieval-index progress will show here.
            </p>
            <IngestionAgenticStructure
              steps={IDLE_STEPS}
              actions={{
                start: {
                  label: "Start",
                  pendingLabel: "Starting…",
                  title: "Choose videos on the Source tab to start Agentic RAG.",
                  onClick: () => router.push(stageHref("source")),
                },
                stop: {
                  label: "Stop",
                  pendingLabel: "Stopping…",
                  disabled: true,
                  title: "There is no active Agentic RAG run to stop.",
                  onClick: () => {},
                },
                wipe: {
                  label: "Wipe",
                  pendingLabel: "Wiping…",
                  disabled: true,
                  title: "There is no Agentic RAG data to wipe until a run starts.",
                  onClick: () => {},
                },
              }}
              onOpenUsage={() => setUsageOpen(true)}
            />
            {usageOpen ? (
              <PipelineUsageSheet
                usage={null}
                title="Agentic RAG LLM usage"
                subtitle="Neo4j graph extraction across the selected videos."
                emptyMessage="No Agentic RAG run yet. Usage appears here after a run starts."
                onClose={() => setUsageOpen(false)}
              />
            ) : null}
          </section>
        )}
      </div>
      <div className="min-w-0">
        <LibraryPanel refreshTick={refreshTick} />
      </div>
    </div>
  );
}
