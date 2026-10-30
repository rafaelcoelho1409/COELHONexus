"use client";

/** The Query stage's top rows: the backend tabs, and under them the editor's header — which store, what it holds and
 * the run status (one polite live line) beside it, and the actions. */

import { BookOpen, History, Play, RotateCcw } from "lucide-react";
import type { JSX } from "react";

import { Button } from "@/shared/ui/button";

import { QUERY_BACKENDS, type QueryBackend } from "../params";

export function BackendTabs({
  backend,
  onChange,
}: {
  backend: QueryBackend;
  onChange: (backend: QueryBackend) => void;
}): JSX.Element {
  return (
    <nav aria-label="Query backend" className="flex flex-wrap gap-x-1">
      {QUERY_BACKENDS.map((entry) => (
        <button
          key={entry.id}
          type="button"
          aria-pressed={entry.id === backend}
          onClick={() => onChange(entry.id)}
          className={
            entry.id === backend
              ? "-mb-px min-h-9 border-b-2 border-primary px-3 py-2 text-sm font-medium text-primary-text"
              : "-mb-px min-h-9 border-b-2 border-transparent px-3 py-2 text-sm text-muted-foreground hover:text-foreground"
          }
        >
          {entry.label}
        </button>
      ))}
    </nav>
  );
}

export function EditorHeader({
  backend,
  caption,
  status,
  running,
  onRun,
  onReset,
  onSchema,
  onHistory,
}: {
  backend: QueryBackend;
  /** What the store holds for YCS ("YCS · metadata + transcripts"); empty until the server answers. */
  caption: string;
  status: string;
  running: boolean;
  onRun: () => void;
  onReset: () => void;
  onSchema: () => void;
  onHistory: () => void;
}): JSX.Element {
  const label = QUERY_BACKENDS.find((entry) => entry.id === backend)?.label ?? backend;
  // On a phone Run sits beside the title and the three secondary actions take the row below; from `sm` up everything
  // shares one row with Run last.
  return (
    <div className="flex flex-wrap items-center gap-2">
      <div className="order-1 mr-auto min-w-0">
        <h2 className="text-base font-semibold">{label}</h2>
        <p className="flex flex-wrap items-center gap-x-1.5 text-xs text-muted-foreground">
          {caption ? (
            <>
              <span>{caption}</span>
              <span aria-hidden>·</span>
            </>
          ) : null}
          <span role="status" className="tabular">
            {status}
          </span>
        </p>
      </div>
      <Button
        size="lg"
        onClick={onRun}
        disabled={running}
        title="Run the query (Ctrl/Cmd+Enter)"
        className="order-2 sm:order-3"
      >
        <Play aria-hidden /> {running ? "Running…" : "Run"}
      </Button>
      <div className="order-3 flex w-full flex-wrap gap-2 sm:order-2 sm:w-auto">
        <Button variant="outline" size="lg" onClick={onReset} title="Put the starting query back">
          <RotateCcw aria-hidden /> Reset
        </Button>
        <Button variant="outline" size="lg" onClick={onSchema}>
          <BookOpen aria-hidden /> Schema
        </Button>
        <Button variant="outline" size="lg" onClick={onHistory}>
          <History aria-hidden /> History
        </Button>
      </div>
    </div>
  );
}
