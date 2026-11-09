"use client";

/** The compact graph/list view for the YCS ingestion workflow. */

import { ArrowDown, ArrowDownLeft, ArrowDownRight } from "lucide-react";
import type { JSX } from "react";
import { useState } from "react";

import { STATUS_WORD_LABEL, statusWord } from "../domain";
import type { Step } from "./pipeline-steps";
import { PipelineSteps } from "./pipeline-steps";
import { SideSheet } from "./side-sheet";
import { StatusBadge } from "./status-badge";

const MODES = ["graph", "list"] as const;

function progressFill(state: string): string {
  const word = statusWord(state);
  if (word === "done") return "bg-success";
  if (word === "partial") return "bg-warning";
  if (word === "failed") return "bg-destructive";
  if (word === "cancelled") return "bg-neutral-8";
  return "bg-primary";
}

function IngestionNode({
  step,
  onOpen,
}: {
  step: Step;
  onOpen: (step: Step) => void;
}): JSX.Element {
  const word = statusWord(step.view.state);
  const progress = Math.max(0, Math.min(100, step.view.pct));
  const indeterminate = word === "running" && progress < 9;
  const label = step.view.label.toLowerCase() === STATUS_WORD_LABEL[word].toLowerCase()
    ? STATUS_WORD_LABEL[word]
    : step.view.label;

  return (
    <button
      type="button"
      onClick={() => onOpen(step)}
      aria-haspopup="dialog"
      data-state={word}
      className={`min-h-9 w-full min-w-0 rounded-sm border bg-card px-3 py-2 text-left hover:bg-muted focus-visible:outline-2 focus-visible:outline-ring ${
        word === "running" ? "border-running" : word === "failed" ? "border-destructive" : "border-border"
      }`}
    >
      <span className="flex min-w-0 items-start justify-between gap-2">
        <span className="min-w-0 text-sm font-medium">{step.title}</span>
        <StatusBadge tone={word} label={STATUS_WORD_LABEL[word]} className="shrink-0" />
      </span>
      <span
        role="progressbar"
        aria-label={step.title}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={indeterminate ? undefined : Math.round(progress)}
        className="mt-2 block h-1.5 w-full overflow-hidden rounded-sm bg-muted"
      >
        <span
          className={`block h-full ${progressFill(step.view.state)} ${indeterminate ? "animate-status" : ""}`}
          style={{ width: `${indeterminate ? 30 : progress}%` }}
        />
      </span>
      <span className="mt-1 block text-xs text-muted-foreground">{label}</span>
    </button>
  );
}

export function IngestionAgenticStructure({ steps }: { steps: ReadonlyArray<Step> }): JSX.Element {
  const [mode, setMode] = useState<(typeof MODES)[number]>("graph");
  const [selected, setSelected] = useState<Step | null>(null);
  const playwright = steps[0];
  const elasticsearch = steps[1];
  const qdrant = steps[2];
  const neo4j = steps[3];

  return (
    <div className="min-w-0">
      <div className="mb-3 flex items-center justify-between gap-2">
        <span className="text-xs font-medium text-muted-foreground">Workflow</span>
        <div role="group" aria-label="Workflow view" className="inline-flex rounded-sm border border-border">
          {MODES.map((entry) => (
            <button
              key={entry}
              type="button"
              aria-pressed={mode === entry}
              onClick={() => setMode(entry)}
              className="min-h-9 min-w-16 px-3 text-xs capitalize aria-pressed:bg-muted aria-pressed:text-foreground"
            >
              {entry}
            </button>
          ))}
        </div>
      </div>

      {mode === "graph" ? (
        <div className="min-w-0">
          <ol aria-label="Pipeline phases" className="grid min-w-0 grid-cols-2 gap-x-2 gap-y-1.5">
            {playwright ? (
              <li className="col-span-2 min-w-0">
                <IngestionNode step={playwright} onOpen={setSelected} />
              </li>
            ) : null}
            {elasticsearch ? (
              <li className="col-span-2 flex min-w-0 flex-col items-center">
                {playwright ? <ArrowDown aria-hidden="true" className="my-1 size-4 text-muted-foreground" /> : null}
                <IngestionNode step={elasticsearch} onOpen={setSelected} />
              </li>
            ) : null}
            {elasticsearch && (qdrant || neo4j) ? (
              <li aria-hidden="true" className="col-span-2 flex h-7 items-center justify-center gap-8 text-muted-foreground">
                <ArrowDownLeft className="size-4" />
                <ArrowDownRight className="size-4" />
              </li>
            ) : null}
            {qdrant ? (
              <li className="min-w-0">
                <IngestionNode step={qdrant} onOpen={setSelected} />
              </li>
            ) : null}
            {neo4j ? (
              <li className="min-w-0">
                <IngestionNode step={neo4j} onOpen={setSelected} />
              </li>
            ) : null}
          </ol>
        </div>
      ) : (
        <PipelineSteps steps={steps} />
      )}

      {selected ? (
        <SideSheet
          title={selected.title}
          subtitle={<StatusBadge tone={statusWord(selected.view.state)} label={STATUS_WORD_LABEL[statusWord(selected.view.state)]} />}
          closeLabel="Close workflow node details"
          onClose={() => setSelected(null)}
        >
          <p className="text-sm text-muted-foreground">{selected.view.hint ?? selected.defaultHint}</p>
          <p className="mt-4 font-mono text-xs text-muted-foreground">{selected.view.label}</p>
        </SideSheet>
      ) : null}
    </div>
  );
}
