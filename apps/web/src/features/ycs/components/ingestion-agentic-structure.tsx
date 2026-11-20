"use client";

/** The YCS retrieval workflow in a graph/list with BD-style node details; run dispatch stays on Source. */

import { ChartNoAxesColumn, ListChecks } from "lucide-react";
import dynamic from "next/dynamic";
import type { JSX } from "react";
import { useState } from "react";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/shared/ui/tabs";

import { STATUS_WORD_LABEL, statusWord } from "../domain";
import type { AskUsage } from "../entities";
import { ActionButton } from "./action-button";
import { IngestionNodeResources } from "./ingestion-node-resources";
import type { Step } from "./pipeline-steps";
import { PipelineSteps } from "./pipeline-steps";
import { PipelineUsageBreakdown } from "./pipeline-usage-sheet";
import { SideSheet } from "./side-sheet";
import { StatusBadge } from "./status-badge";

const IngestionAgenticFlow = dynamic(
  () => import("./ingestion-agentic-flow").then((module) => module.IngestionAgenticFlow),
  {
    ssr: false,
    loading: () => <p className="text-xs text-muted-foreground">Loading graph…</p>,
  },
);

const MODES = [
  { id: "graph", label: "Graph" },
  { id: "list", label: "List" },
] as const;

const DEFAULT_CAPTION =
  "Playwright retrieves transcripts → Elasticsearch indexes them → Qdrant writes vectors while Neo4j extracts the retrieval graph.";

export function IngestionAgenticStructure({
  steps,
  onOpenUsage,
  onOpenVideos,
  videoCount = 0,
  usage = null,
  usageLoading = false,
  usageError = null,
  ariaLabel = "Agentic RAG pipeline",
  caption = DEFAULT_CAPTION,
  onPlayNode,
  replayDisabled = false,
  replayPending = false,
  replayPendingNodeId = null,
}: {
  steps: ReadonlyArray<Step>;
  onOpenUsage: () => void;
  onOpenVideos?: () => void;
  videoCount?: number;
  usage?: AskUsage | null;
  usageLoading?: boolean;
  usageError?: string | null;
  ariaLabel?: string;
  caption?: string;
  onPlayNode?: (nodeId: string) => void;
  replayDisabled?: boolean;
  replayPending?: boolean;
  replayPendingNodeId?: string | null;
}): JSX.Element {
  const [mode, setMode] = useState<(typeof MODES)[number]["id"]>("graph");
  const [selectedKey, setSelectedKey] = useState<string | null>(null);
  const selected = steps.find((step) => step.key === selectedKey);

  return (
    <>
      <div className="flex min-w-0 flex-col gap-3">
        <div className={`grid min-w-0 gap-2 ${onOpenVideos ? "grid-cols-2" : "grid-cols-1"}`}>
          <ActionButton
            type="button"
            aria-haspopup="dialog"
            onClick={onOpenUsage}
            className="w-full justify-center whitespace-nowrap px-2"
          >
            <ChartNoAxesColumn aria-hidden />
            LLM usage
          </ActionButton>
          {onOpenVideos ? (
            <ActionButton
              type="button"
              aria-haspopup="dialog"
              onClick={onOpenVideos}
              className="w-full justify-center whitespace-nowrap px-2"
            >
              <ListChecks aria-hidden />
              {`Videos (${videoCount})`}
            </ActionButton>
          ) : null}
        </div>

        <section
          aria-label={ariaLabel}
          className="flex min-w-0 flex-col gap-3 rounded-md border border-border bg-card p-3"
        >
          <header>
            <h3 className="text-sm font-medium">Agentic RAG pipeline</h3>
            <p aria-live="polite" title={caption} className="mt-1 text-xs leading-4 text-muted-foreground">
              {caption}
            </p>
          </header>

          <fieldset className="m-0 flex w-full overflow-hidden rounded-sm border border-border bg-background p-0">
            <legend className="sr-only">Agentic RAG workflow shown as</legend>
            {MODES.map((entry) => (
              <ActionButton
                key={entry.id}
                type="button"
                aria-pressed={mode === entry.id}
                onClick={() => setMode(entry.id)}
                className="min-h-9 min-w-0 flex-1 justify-center whitespace-nowrap rounded-none border-0 border-r border-r-border px-1.5 last:border-r-0"
              >
                {entry.label}
              </ActionButton>
            ))}
          </fieldset>

          {mode === "graph" ? (
            <IngestionAgenticFlow
              steps={steps}
              onOpen={setSelectedKey}
              onPlay={onPlayNode}
              replayDisabled={replayDisabled}
              replayPending={replayPending}
              replayPendingNodeId={replayPendingNodeId}
            />
          ) : mode === "list" ? (
            <PipelineSteps
              steps={steps}
              onOpen={(step) => setSelectedKey(step.key)}
              onPlay={onPlayNode ? (step) => onPlayNode(step.key) : undefined}
              replayDisabled={replayDisabled}
              replayPending={replayPending}
              replayPendingStepKey={replayPendingNodeId}
            />
          ) : null}
        </section>
      </div>

      {selected ? (
        <SideSheet
          key={selected.key}
          title={selected.title}
          subtitle={
            <StatusBadge
              tone={statusWord(selected.view.state)}
              label={STATUS_WORD_LABEL[statusWord(selected.view.state)]}
            />
          }
          closeLabel="Close ingestion node details"
          onClose={() => setSelectedKey(null)}
        >
          <Tabs defaultValue="overview" className="w-full">
            <TabsList aria-label="Agentic RAG node details">
              <TabsTrigger value="overview">Overview</TabsTrigger>
              <TabsTrigger value="llm-calls">LLM Calls</TabsTrigger>
              <TabsTrigger value="resources">Resources</TabsTrigger>
            </TabsList>
            <TabsContent value="overview" className="flex flex-col gap-3 pt-4">
              <section aria-label="Node purpose" className="rounded-sm border border-border bg-card p-3">
                <h3 className="text-sm font-medium">What this node does</h3>
                <p className="mt-2 text-sm text-muted-foreground">{selected.defaultHint}</p>
              </section>
              <dl className="grid grid-cols-[3.5rem_minmax(0,1fr)] gap-x-3 gap-y-2 text-xs">
                <dt className="text-muted-foreground">Reads</dt>
                <dd className="break-words">{selected.reads.join(", ")}</dd>
                <dt className="text-muted-foreground">Writes</dt>
                <dd className="break-words">{selected.writes.join(", ")}</dd>
              </dl>
              <dl className="grid grid-cols-1 gap-2 sm:grid-cols-2">
                <div className="rounded-sm border border-border px-3 py-2">
                  <dt className="text-xs text-muted-foreground">Current state</dt>
                  <dd className="mt-1 text-sm font-medium">
                    {STATUS_WORD_LABEL[statusWord(selected.view.state)]}
                  </dd>
                </div>
                <div className="rounded-sm border border-border px-3 py-2">
                  <dt className="text-xs text-muted-foreground">Latest update</dt>
                  <dd className="mt-1 break-words font-mono text-xs">{selected.view.label}</dd>
                </div>
              </dl>
              {selected.view.hint ? (
                <p className="text-sm text-muted-foreground">{selected.view.hint}</p>
              ) : null}
            </TabsContent>
            <TabsContent value="llm-calls" className="flex flex-col gap-3 pt-4">
              {selected.key === "neo4j" ? (
                <>
                  <p className="text-sm text-muted-foreground">
                    Neo4j extraction calls are attributed to the source video they process.
                  </p>
                  <PipelineUsageBreakdown
                    usage={usage}
                    loading={usageLoading}
                    error={usageError}
                    emptyMessage="No Neo4j graph-extraction calls have been recorded for this run."
                  />
                </>
              ) : (
                <p className="text-sm text-muted-foreground">This node does not make LLM calls.</p>
              )}
            </TabsContent>
            <TabsContent value="resources" className="pt-4">
              <section aria-label="Agentic resources" className="rounded-sm border border-border bg-card p-3">
                <IngestionNodeResources nodeId={selected.key} />
              </section>
            </TabsContent>
          </Tabs>
        </SideSheet>
      ) : null}
    </>
  );
}
