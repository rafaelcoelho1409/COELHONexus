"use client";

/** The four phases of a run as a row of steps (a column on a phone): each names itself, says in an icon and a word where it
 * stands, draws its progress (a bar that only pulses while the work has no count yet) and says in one line what it did. The
 * bars are `progressbar`s with a name and a value; they are not live regions, so a poll every 0.7 s says nothing to a screen
 * reader — the panel's one status line announces the transitions. */

import type { JSX } from "react";

import { STATUS_WORD_LABEL, statusWord } from "../domain";
import { StatusBadge } from "./status-badge";

export type StepView = {
  /** Lower-cased Celery state, or the outcome of a finished phase (success / partial / failure / cancelled…). */
  state: string;
  pct: number;
  /** "3/10", "Done", "Failed: …" — the phase's own words. */
  label: string;
  hint: string | null;
};

export type Step = { key: string; title: string; defaultHint: string; view: StepView };

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

export function PipelineSteps({ steps }: { steps: ReadonlyArray<Step> }): JSX.Element {
  return (
    <ol aria-label="Pipeline phases" className="grid grid-cols-1 gap-2 md:grid-cols-4">
      {steps.map((step) => {
        const word = statusWord(step.view.state);
        const wordLabel = STATUS_WORD_LABEL[word];
        const indeterminate = word === "running" && step.view.pct < INDETERMINATE_BELOW;
        const pct = Math.max(0, Math.min(100, step.view.pct));
        const showLabel = step.view.label.toLowerCase() !== wordLabel.toLowerCase();
        return (
          <li
            key={step.key}
            data-state={word}
            className="flex min-w-0 flex-col gap-2 rounded-md border border-border bg-card p-3"
          >
            <div className="flex items-start justify-between gap-2">
              <h3 className="min-w-0 text-sm font-medium">{step.title}</h3>
              <StatusBadge tone={word} label={wordLabel} />
            </div>
            <div
              role="progressbar"
              aria-label={step.title}
              aria-valuemin={0}
              aria-valuemax={100}
              aria-valuenow={indeterminate ? undefined : Math.round(pct)}
              className="h-1.5 w-full overflow-hidden rounded-sm bg-muted"
            >
              <div
                className={`h-full ${FILL[step.view.state] ?? "bg-primary"} ${indeterminate ? "animate-status" : ""}`}
                style={{ width: `${indeterminate ? 30 : pct}%` }}
              />
            </div>
            {showLabel ? <p className="tabular font-mono text-xs break-words">{step.view.label}</p> : null}
            <p className="text-xs break-words text-muted-foreground">{step.view.hint ?? step.defaultHint}</p>
          </li>
        );
      })}
    </ol>
  );
}
