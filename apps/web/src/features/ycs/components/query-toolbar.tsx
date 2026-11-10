"use client";

/** The Query stage's top rows: the backend tabs, and under them the editor's header — which store, what it holds and
 * the run status (one polite live line) beside it, and the actions. */

import { BookOpen, History, Play, RotateCcw } from "lucide-react";
import type { JSX } from "react";

import { QUERY_BACKENDS, type QueryBackend } from "../params";
import { ActionButton } from "./action-button";

export function BackendTabs({
  backend,
  onChange,
}: {
  backend: QueryBackend;
  onChange: (backend: QueryBackend) => void;
}): JSX.Element {
  return (
    <nav
      aria-label="Query backend"
      className="inline-flex max-w-full flex-nowrap overflow-x-auto rounded-sm border border-border bg-background"
    >
      {QUERY_BACKENDS.map((entry) => (
        <ActionButton
          key={entry.id}
          type="button"
          aria-pressed={entry.id === backend}
          onClick={() => onChange(entry.id)}
          className="min-h-9 shrink-0 rounded-none border-0 border-r border-r-border px-3 py-2 text-sm last:border-r-0"
        >
          {entry.label}
        </ActionButton>
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
      <ActionButton
        onClick={onRun}
        disabled={running}
        title="Run the query (Ctrl/Cmd+Enter)"
        className="order-2 sm:order-3"
      >
        <Play aria-hidden /> {running ? "Running…" : "Run"}
      </ActionButton>
      <div className="order-3 flex w-full flex-wrap gap-2 sm:order-2 sm:w-auto">
        <ActionButton onClick={onReset} title="Put the starting query back">
          <RotateCcw aria-hidden /> Reset
        </ActionButton>
        <ActionButton onClick={onSchema}>
          <BookOpen aria-hidden /> Schema
        </ActionButton>
        <ActionButton onClick={onHistory}>
          <History aria-hidden /> History
        </ActionButton>
      </div>
    </div>
  );
}
