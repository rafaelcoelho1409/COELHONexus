"use client";

/** The Query stage's backend tabs, editor context, and full-width action row. */

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
    <div aria-label="Query backend" role="tablist" className="ml-auto flex flex-wrap items-center">
      {QUERY_BACKENDS.map((entry) => (
        <button
          key={entry.id}
          type="button"
          role="tab"
          aria-selected={entry.id === backend}
          onClick={() => onChange(entry.id)}
          className={`inline-flex h-9 items-center border-b-2 px-2 text-sm transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 sm:px-3 ${
            entry.id === backend
              ? "border-primary font-semibold text-foreground"
              : "border-transparent font-medium text-muted-foreground hover:border-border hover:text-foreground"
          }`}
        >
          {entry.label}
        </button>
      ))}
    </div>
  );
}

export function EditorHeader({
  backend,
  caption,
  status,
}: {
  backend: QueryBackend;
  /** What the store holds for YCS ("YCS · metadata + transcripts"); empty until the server answers. */
  caption: string;
  status: string;
}): JSX.Element {
  const label = QUERY_BACKENDS.find((entry) => entry.id === backend)?.label ?? backend;
  return (
    <div className="min-w-0">
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
  );
}

export function EditorActions({
  backendLabel,
  queryRunning,
  queryDisabled,
  onReset,
  onSchema,
  onHistory,
  onRun,
}: {
  backendLabel: string;
  queryRunning: boolean;
  queryDisabled: boolean;
  onReset: () => void;
  onSchema: () => void;
  onHistory: () => void;
  onRun: () => void;
}): JSX.Element {
  const buttonClass = "w-full min-w-0 justify-center gap-2 whitespace-nowrap";

  return (
    <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
      <ActionButton onClick={onReset} title="Put the starting query back" className={buttonClass}>
        <RotateCcw aria-hidden /> Reset
      </ActionButton>
      <ActionButton onClick={onSchema} className={buttonClass}>
        <BookOpen aria-hidden /> Schema
      </ActionButton>
      <ActionButton onClick={onHistory} className={buttonClass}>
        <History aria-hidden /> History
      </ActionButton>
      <ActionButton
        onClick={onRun}
        disabled={queryRunning || queryDisabled}
        title={`Run the ${backendLabel} query (Ctrl/Cmd+Enter)`}
        className={buttonClass}
      >
        <Play aria-hidden /> {queryRunning ? "Running…" : "Run"}
      </ActionButton>
    </div>
  );
}
