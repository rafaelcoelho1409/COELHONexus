"use client";

/** The ingestion workflow in the same graph/list and node-detail pattern as the other Agentic AI stages. */

import { ChartNoAxesColumn } from "lucide-react";
import dynamic from "next/dynamic";
import type { JSX } from "react";
import { useState } from "react";

import { MermaidTopology } from "@/shared/markdown/mermaid-topology";

import { STATUS_WORD_LABEL, statusWord } from "../domain";
import { ActionButton } from "./action-button";
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
  { id: "langgraph", label: "LangGraph" },
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

const INGESTION_TOPOLOGY = `flowchart TD
  start([Start]) --> playwright["Playwright transcripts"]
  playwright --> elasticsearch["Elasticsearch indexing"]
  elasticsearch --> qdrant["Qdrant vector index"]
  qdrant --> neo4j["Neo4j entity graph"]
  neo4j --> finish([End])`;

export function IngestionAgenticStructure({
  steps,
  actions,
  onOpenUsage,
  ariaLabel = "Agentic ingestion workflow",
  caption = "Target flow: Playwright transcript retrieval → Elasticsearch → Qdrant and Neo4j.",
}: {
  steps: ReadonlyArray<Step>;
  actions: IngestionWorkflowActions;
  onOpenUsage: () => void;
  ariaLabel?: string;
  caption?: string;
}): JSX.Element {
  const [mode, setMode] = useState<(typeof MODES)[number]["id"]>("graph");
  const [selectedKey, setSelectedKey] = useState<string | null>(null);
  const selected = steps.find((step) => step.key === selectedKey);

  return (
    <>
      <section
        aria-label={ariaLabel}
        className="flex min-w-0 flex-col gap-3 rounded-md border border-border bg-card p-3"
      >
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

        <fieldset className="m-0 flex w-full overflow-hidden rounded-sm border border-border bg-background p-0">
          <legend className="sr-only">Ingestion workflow shown as</legend>
          {MODES.map((entry) => (
            <ActionButton
              key={entry.id}
              type="button"
              aria-pressed={mode === entry.id}
              onClick={() => setMode(entry.id)}
              className="min-w-0 flex-1 justify-center whitespace-nowrap rounded-none border-0 border-r border-r-border px-1.5 last:border-r-0"
            >
              {entry.label}
            </ActionButton>
          ))}
        </fieldset>

        <p
          aria-live="polite"
          title={caption}
          className="line-clamp-2 text-xs leading-4 text-muted-foreground"
        >
          {caption}
        </p>

        {mode === "graph" ? (
          <IngestionAgenticFlow steps={steps} onOpen={setSelectedKey} />
        ) : mode === "list" ? (
          <PipelineSteps steps={steps} onOpen={(step) => setSelectedKey(step.key)} />
        ) : (
          <MermaidTopology
            title="YCS ingestion workflow"
            source={INGESTION_TOPOLOGY}
            note="YCS extraction runs as task phases, not a LangGraph. This shows its current phase sequence."
          />
        )}
      </section>

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
          <section aria-label="Node purpose" className="rounded-sm border border-border bg-card p-3">
            <h3 className="text-sm font-medium">What this node does</h3>
            <p className="mt-2 text-sm text-muted-foreground">{selected.defaultHint}</p>
          </section>
          <dl className="mt-3 grid grid-cols-1 gap-2 sm:grid-cols-2">
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
            <p className="mt-3 text-sm text-muted-foreground">{selected.view.hint}</p>
          ) : null}
        </SideSheet>
      ) : null}
    </>
  );
}
