"use client";

/** The ingestion workflow in the same graph/list and node-detail pattern as the other Agentic AI stages. */

import dynamic from "next/dynamic";
import type { JSX } from "react";
import { useState } from "react";

import { STATUS_WORD_LABEL, statusWord } from "../domain";
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

export function IngestionAgenticStructure({
  steps,
  ariaLabel = "Agentic ingestion workflow",
  caption = "Target flow: Playwright transcript retrieval → Elasticsearch → Qdrant and Neo4j.",
}: {
  steps: ReadonlyArray<Step>;
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
        <header className="flex min-h-9 min-w-0 items-center justify-between gap-3">
          <p
            aria-live="polite"
            title={caption}
            className="line-clamp-2 min-w-0 text-xs leading-4 text-muted-foreground"
          >
            {caption}
          </p>
          <fieldset className="m-0 inline-flex shrink-0 rounded-sm border border-border p-0">
            <legend className="sr-only">Ingestion workflow shown as</legend>
            {MODES.map((entry) => (
              <button
                key={entry.id}
                type="button"
                aria-pressed={mode === entry.id}
                onClick={() => setMode(entry.id)}
                className="min-h-9 min-w-16 px-3 text-xs aria-pressed:bg-muted aria-pressed:text-foreground focus-visible:outline-2 focus-visible:outline-ring"
              >
                {entry.label}
              </button>
            ))}
          </fieldset>
        </header>

        {mode === "graph" ? (
          <IngestionAgenticFlow steps={steps} onOpen={setSelectedKey} />
        ) : (
          <PipelineSteps steps={steps} onOpen={(step) => setSelectedKey(step.key)} />
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
