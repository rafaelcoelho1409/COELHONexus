"use client";

/** The four phases of a run as a row of steps (a column on a phone): each names itself, says in an icon and a word where it
 * stands, draws its progress (a bar that only pulses while the work has no count yet) and says in one line what it did. The
 * bars are `progressbar`s with a name and a value; they are not live regions, so a poll every 0.7 s says nothing to a screen
 * reader — the panel's one status line announces the transitions. */

import { Play } from "lucide-react";
import type { JSX } from "react";

import { STATUS_WORD_LABEL, statusWord } from "../domain";
import { IngestionStatusGlyph } from "./ingestion-status-glyph";
import { StatusBadge } from "./status-badge";

export type StepView = {
  /** Lower-cased Celery state, or the outcome of a finished phase (success / partial / failure / cancelled…). */
  state: string;
  pct: number;
  /** "3/10", "Done", "Failed: …" — the phase's own words. */
  label: string;
  hint: string | null;
};

export type Step = {
  key: string;
  title: string;
  defaultHint: string;
  reads: readonly string[];
  writes: readonly string[];
  view: StepView;
};

const FILL: Record<string, string> = {
  success: "bg-success",
  partial: "bg-warning",
  failure: "bg-destructive",
  error: "bg-destructive",
  cancelled: "bg-neutral-8",
  revoked: "bg-neutral-8",
};

/** Work that has begun but has no count to measure it by shows as a pulsing stub, not as a number it cannot back up. */
const INDETERMINATE_BELOW = 9;

export function StepProgress({ step }: { step: Step }): JSX.Element {
  const word = statusWord(step.view.state);
  const indeterminate = word === "running" && step.view.pct < INDETERMINATE_BELOW;
  const pct = Math.max(0, Math.min(100, step.view.pct));
  return (
    <span
      role="progressbar"
      aria-label={step.title}
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={indeterminate ? undefined : Math.round(pct)}
      className="mt-2 block h-1.5 w-full overflow-hidden rounded-sm bg-muted"
    >
      <span
        className={`block h-full ${FILL[step.view.state] ?? "bg-primary"} ${indeterminate ? "animate-status" : ""}`}
        style={{ width: `${indeterminate ? 30 : pct}%` }}
      />
    </span>
  );
}

export function PipelineSteps({
  steps,
  onOpen,
  onPlay,
}: {
  steps: ReadonlyArray<Step>;
  onOpen: (step: Step) => void;
  onPlay?: (step: Step) => void;
}): JSX.Element {
  return (
    <ol aria-label="Agentic RAG stages" className="flex flex-col gap-1.5">
      {steps.map((step) => {
        const word = statusWord(step.view.state);
        const wordLabel = STATUS_WORD_LABEL[word];
        return (
          <li key={step.key} data-node={step.key} data-status={word}>
            <div
              className={`flex min-w-0 items-stretch gap-1.5 rounded-sm border bg-card p-1.5 ${
                word === "running"
                  ? "border-running"
                  : word === "failed"
                    ? "border-destructive"
                    : word === "partial"
                      ? "border-warning"
                      : "border-border"
              }`}
            >
              <button
                type="button"
                onClick={() => onOpen(step)}
                aria-haspopup="dialog"
                aria-label={`Open ${step.title} node details`}
                className="min-h-9 min-w-0 flex-1 rounded-sm px-1.5 py-1 text-left hover:bg-muted focus-visible:outline-2 focus-visible:outline-ring"
              >
                <span className="flex items-center justify-between gap-3">
                  <span className="flex min-w-0 items-center gap-1.5">
                    <IngestionStatusGlyph status={word} />
                    <span className="min-w-0 text-sm">{step.title}</span>
                  </span>
                  <StatusBadge tone={word} label={wordLabel} className="shrink-0" />
                </span>
                <StepProgress step={step} />
                {step.view.label !== wordLabel ? (
                  <span className="mt-1 block font-mono text-xs break-words text-muted-foreground">
                    {step.view.label}
                  </span>
                ) : null}
                <span className="mt-1 block text-xs break-words text-muted-foreground">
                  {step.view.hint ?? step.defaultHint}
                </span>
              </button>
              {onPlay ? (
                <button
                  type="button"
                  onClick={(event) => {
                    event.stopPropagation();
                    onPlay(step);
                  }}
                  aria-label={`Replay from ${step.title}`}
                  title={`Replay from ${step.title} and clear downstream results`}
                  className="inline-flex size-9 shrink-0 items-center justify-center self-start rounded-sm text-muted-foreground hover:bg-muted hover:text-foreground focus-visible:outline-2 focus-visible:outline-ring"
                >
                  <Play aria-hidden className="size-4" />
                </button>
              ) : null}
            </div>
          </li>
        );
      })}
    </ol>
  );
}
