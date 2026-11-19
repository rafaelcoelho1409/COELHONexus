"use client";

/** The Query stage's top rows: backend tabs, editor context, and full-width actions. */

import { BookOpen, History, Play, RotateCcw } from "lucide-react";
import type { JSX } from "react";
import { QUERY_BACKENDS, type QueryBackend } from "../params";
import { RagActionButton as Button } from "./rag-action-button";

const BACKEND_TAB_CLASS = [
  "inline-flex h-9 items-center px-2 text-[0.875rem] transition-colors",
  "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 sm:px-3",
].join(" ");

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
          className={`${BACKEND_TAB_CLASS} ${
            entry.id === backend
              ? "font-semibold text-foreground"
              : "font-medium text-muted-foreground hover:text-foreground"
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
  /** What the store holds for Book Distiller; empty until the server answers. */
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
  queryRunning,
  queryDisabled,
  onReset,
  onSchema,
  onHistory,
  onRun,
}: {
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
      <Button
        variant="outline"
        size="lg"
        onClick={onReset}
        title="Put the starting query back"
        className={buttonClass}
      >
        <RotateCcw aria-hidden /> Reset
      </Button>
      <Button variant="outline" size="lg" onClick={onSchema} className={buttonClass}>
        <BookOpen aria-hidden /> Schema
      </Button>
      <Button variant="outline" size="lg" onClick={onHistory} className={buttonClass}>
        <History aria-hidden /> History
      </Button>
      <Button
        size="lg"
        onClick={onRun}
        disabled={queryRunning || queryDisabled}
        title="Run the query (Ctrl/Cmd+Enter)"
        className={buttonClass}
      >
        <Play aria-hidden /> {queryRunning ? "Running…" : "Run"}
      </Button>
    </div>
  );
}
