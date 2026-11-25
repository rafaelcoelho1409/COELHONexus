"use client";

/** Ingestion stage: the live pipeline panel (when a dispatch is being tracked) above the Library. With nothing running the panel
 * gives way to one line — the Library is what this stage is for, so it is not pushed down by an empty card.
 * Search params come in as props — Next only gives them to a component inside a Suspense boundary. */

import { Info } from "lucide-react";
import Link from "next/link";
import type { JSX } from "react";
import { useState } from "react";

import { usePipelineTracking } from "../hooks/use-pipeline-tracking";
import { stageHref } from "../params";
import { ActionButton } from "./action-button";
import { IngestionAgenticStructure } from "./ingestion-agentic-structure";
import { LibraryPanel } from "./library-panel";
import { PipelinePanel } from "./pipeline-panel";
import type { Step } from "./pipeline-steps";
import { PipelineUsageSheet } from "./pipeline-usage-sheet";
import { SideSheet } from "./side-sheet";
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
    defaultHint: "Docling Graph extraction, transcript-evidence validation, and Neo4j graph writes.",
    reads: ["Transcripts", "Video metadata"],
    writes: ["Knowledge graph", "Evidence-linked relationships"],
    view: { state: "pending", pct: 0, label: "Waiting", hint: null },
  },
];

export function IngestionStage(): JSX.Element {
  const { tracking } = usePipelineTracking();
  // Library refresh ticks: one per phase reaching SUCCESS, then a final sweep when the chain ends.
  const [refreshTick, setRefreshTick] = useState(0);
  const [usageOpen, setUsageOpen] = useState(false);
  const [detailsOpen, setDetailsOpen] = useState(false);
  const bump = () => setRefreshTick((tick) => tick + 1);

  return (
    <div className="grid min-w-0 grid-cols-1 items-start gap-6 xl:grid-cols-[minmax(0,35fr)_minmax(0,65fr)]">
      <div className="min-w-0">
        {tracking ? (
          // A new dispatch or retry starts with fresh progress and node selection.
          <PipelinePanel key={tracking.extract} tracking={tracking} onPhaseDone={bump} onAllDone={bump} />
        ) : (
          <section aria-label="Agentic RAG ingestion structure" className="flex min-w-0 flex-col gap-3">
            <header className="flex items-start gap-2">
              <div className="flex min-w-0 flex-1 flex-wrap items-center gap-2">
                <h2 className="text-base font-medium">Agentic RAG</h2>
                <StatusBadge tone="queued" label="Waiting for a run" />
              </div>
              <ActionButton
                type="button"
                iconOnly
                aria-haspopup="dialog"
                aria-label="Open Agentic RAG details"
                title="Agentic RAG details"
                onClick={() => setDetailsOpen(true)}
                className="shrink-0"
              >
                <Info aria-hidden />
              </ActionButton>
            </header>
            <p className="text-sm text-muted-foreground">
              No Agentic RAG run is active. Choose videos on the{" "}
              <Link href={stageHref("source")} className="text-primary-text underline underline-offset-2">
                Source tab
              </Link>
              ; its ingestion and retrieval-index progress will show here.
            </p>
            <IngestionAgenticStructure steps={IDLE_STEPS} onOpenUsage={() => setUsageOpen(true)} />
            {usageOpen ? (
              <PipelineUsageSheet
                usage={null}
                title="Agentic RAG LLM usage"
                subtitle="Neo4j graph extraction across the selected videos."
                emptyMessage="No Agentic RAG run yet. Usage appears here after a run starts."
                onClose={() => setUsageOpen(false)}
              />
            ) : null}
            {detailsOpen ? (
              <SideSheet
                title="Agentic RAG details"
                subtitle="Waiting for a run"
                closeLabel="Close Agentic RAG details"
                onClose={() => setDetailsOpen(false)}
              >
                <section
                  aria-label="Agentic RAG overview"
                  className="rounded-sm border border-border bg-card p-4"
                >
                  <h3 className="text-sm font-medium">Run from Source</h3>
                  <p className="mt-2 text-sm text-muted-foreground">
                    Agentic RAG processes the videos selected and ingested on the Source tab. This graph shows
                    transcript retrieval, search indexing, and the parallel vector and knowledge-graph writes.
                  </p>
                </section>
              </SideSheet>
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
