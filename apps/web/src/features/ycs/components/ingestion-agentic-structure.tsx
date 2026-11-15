"use client";

/** The ingestion workflow in the same graph/list and node-detail pattern as the other Agentic AI stages. */

import { ChartNoAxesColumn } from "lucide-react";
import dynamic from "next/dynamic";
import type { JSX } from "react";
import { useState } from "react";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/shared/ui/tabs";

import { STATUS_WORD_LABEL, statusWord } from "../domain";
import { ActionButton } from "./action-button";
import { IngestionNodeResources } from "./ingestion-node-resources";
import type { Step } from "./pipeline-steps";
import { PipelineSteps } from "./pipeline-steps";
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

export type IngestionWorkflowAction = {
  label: string;
  pendingLabel: string;
  pending?: boolean;
  disabled?: boolean;
  title?: string;
  onClick: () => void;
};

export type IngestionWorkflowActions = {
  start: IngestionWorkflowAction;
  stop: IngestionWorkflowAction;
  wipe: IngestionWorkflowAction;
};

const DEFAULT_CAPTION =
  "Playwright retrieves transcripts → Elasticsearch indexes them → Qdrant writes vectors while Neo4j writes graph data.";

export function IngestionAgenticStructure({
  steps,
  actions,
  onOpenUsage,
  ariaLabel = "Agentic ingestion workflow",
  caption = DEFAULT_CAPTION,
  onPlayNode,
}: {
  steps: ReadonlyArray<Step>;
  actions: IngestionWorkflowActions;
  onOpenUsage: () => void;
  ariaLabel?: string;
  caption?: string;
  onPlayNode?: (nodeId: string) => void;
}): JSX.Element {
  const [mode, setMode] = useState<(typeof MODES)[number]["id"]>("graph");
  const [selectedKey, setSelectedKey] = useState<string | null>(null);
  const selected = steps.find((step) => step.key === selectedKey);

  return (
    <>
      <div className="flex min-w-0 flex-col gap-3">
        <fieldset className="m-0 grid min-w-0 grid-cols-3 gap-2 border-0 p-0">
          <legend className="sr-only">Ingestion run controls</legend>
          {(["start", "stop", "wipe"] as const).map((key) => {
            const action = actions[key];
            return (
              <ActionButton
                key={key}
                type="button"
                onClick={action.onClick}
                disabled={action.disabled}
                aria-busy={action.pending}
                title={action.title}
                className="min-w-0 justify-center whitespace-nowrap px-2"
              >
                {action.pending ? action.pendingLabel : action.label}
              </ActionButton>
            );
          })}
        </fieldset>

        <ActionButton
          type="button"
          aria-haspopup="dialog"
          onClick={onOpenUsage}
          className="w-full justify-center whitespace-nowrap px-2"
        >
          <ChartNoAxesColumn aria-hidden />
          LLM usage
        </ActionButton>

        <section
          aria-label={ariaLabel}
          className="flex min-w-0 flex-col gap-3 rounded-md border border-border bg-card p-3"
        >
          <header>
            <h3 className="text-sm font-medium">Agentic pipeline</h3>
            <p aria-live="polite" title={caption} className="mt-1 text-xs leading-4 text-muted-foreground">
              {caption}
            </p>
          </header>

          <fieldset className="m-0 flex w-full overflow-hidden rounded-sm border border-border bg-background p-0">
            <legend className="sr-only">Ingestion workflow shown as</legend>
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
            />
          ) : mode === "list" ? (
            <PipelineSteps
              steps={steps}
              onOpen={(step) => setSelectedKey(step.key)}
              onPlay={onPlayNode ? (step) => onPlayNode(step.key) : undefined}
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
            <TabsList aria-label="Node details">
              <TabsTrigger value="overview">Overview</TabsTrigger>
              <TabsTrigger value="resources">Resources</TabsTrigger>
            </TabsList>
            <TabsContent value="overview" className="flex flex-col gap-3 pt-4">
              <section aria-label="Node purpose" className="rounded-sm border border-border bg-card p-3">
                <h3 className="text-sm font-medium">What this node does</h3>
                <p className="mt-2 text-sm text-muted-foreground">{selected.defaultHint}</p>
              </section>
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
              {selected.view.hint ? <p className="text-sm text-muted-foreground">{selected.view.hint}</p> : null}
            </TabsContent>
            <TabsContent value="resources" className="pt-4">
              <section aria-label="Agent, skills, and tools" className="rounded-sm border border-border bg-card p-3">
                <IngestionNodeResources nodeId={selected.key} />
              </section>
            </TabsContent>
          </Tabs>
        </SideSheet>
      ) : null}
    </>
  );
}
