"use client";

/** Parts shared by the Agentic AI graph, its list and its node sheet. They draw a node the way the Docs Distiller's
 * stage graphs do: a glyph for the state, the state also in words, a headline number once the node is done and a
 * monospace line while it runs. */

import { Check, Circle, CircleDot, X } from "lucide-react";

import type { AgenticNodeStatus, AgenticNodeView } from "../domain/agentic-progress";

const GLYPH_CLASS = "size-3 shrink-0";

export function AgenticStatusGlyph({ status }: { status: AgenticNodeStatus }) {
  if (status === "done") return <Check aria-hidden className={`${GLYPH_CLASS} text-success-text`} />;
  if (status === "running") {
    return <CircleDot aria-hidden className={`${GLYPH_CLASS} animate-pulse text-running-text`} />;
  }
  if (status === "failed") return <X aria-hidden className={`${GLYPH_CLASS} text-destructive-text`} />;
  return <Circle aria-hidden className={`${GLYPH_CLASS} text-muted-foreground`} />;
}

export const AGENTIC_STATUS_LABEL: Record<AgenticNodeStatus, string> = {
  done: "done",
  running: "running",
  pending: "pending",
  failed: "failed",
};

/** Glyph plus the state in words, so it never rests on the icon or its colour alone. */
export function AgenticStatusBadge({ status }: { status: AgenticNodeStatus }) {
  return (
    <span className="inline-flex items-center gap-1.5 text-xs text-muted-foreground">
      <AgenticStatusGlyph status={status} />
      {AGENTIC_STATUS_LABEL[status]}
    </span>
  );
}

/** The graph's text equivalent: the same buttons in run order, one per row, nothing to pan or zoom. */
export function AgenticNodeList({
  rows,
  onOpen,
}: {
  rows: readonly AgenticNodeView[];
  onOpen: (nodeId: string) => void;
}) {
  return (
    <ol aria-label="Agentic pipeline nodes" className="flex flex-col gap-1.5">
      {rows.map((row) => (
        <li key={row.id} data-status={row.status} data-node={row.id}>
          <button
            type="button"
            onClick={() => onOpen(row.id)}
            aria-haspopup="dialog"
            className={`block min-h-9 w-full rounded-sm border bg-card px-3 py-2 text-left hover:bg-muted focus-visible:outline-2 focus-visible:outline-ring ${
              row.status === "running"
                ? "border-running"
                : row.status === "failed"
                  ? "border-destructive"
                  : "border-border"
            }`}
          >
            <span className="flex items-center justify-between gap-3">
              <span className="flex min-w-0 items-center gap-1.5">
                <AgenticStatusGlyph status={row.status} />
                <span className="text-sm">{row.label}</span>
                <span className="sr-only">— {AGENTIC_STATUS_LABEL[row.status]}</span>
              </span>
              {row.kpi ? (
                <span className="min-w-0 truncate text-xs text-muted-foreground tabular-nums">{row.kpi}</span>
              ) : null}
            </span>
            {row.liveLine ? (
              <span className="mt-1 block font-mono text-xs break-all text-muted-foreground">
                {row.liveLine}
              </span>
            ) : null}
          </button>
        </li>
      ))}
    </ol>
  );
}

const AGENTIC_VIEWS = [
  { id: "graph", label: "Graph" },
  { id: "list", label: "List" },
] as const;
export type AgenticViewId = (typeof AGENTIC_VIEWS)[number]["id"];

/** The Graph | List switch, drawn as the stage graphs' own: two joined buttons, the chosen one tinted. */
export function AgenticViewSwitch({
  value,
  onChange,
}: {
  value: AgenticViewId;
  onChange: (view: AgenticViewId) => void;
}) {
  return (
    <fieldset className="m-0 flex min-w-0 shrink-0 border-0 p-0">
      <legend className="sr-only">Pipeline shown as</legend>
      {AGENTIC_VIEWS.map((view) => (
        <button
          key={view.id}
          type="button"
          aria-pressed={value === view.id}
          onClick={() => onChange(view.id)}
          className={`min-h-9 border px-3 text-xs first:rounded-l-sm last:rounded-r-sm ${
            value === view.id
              ? "border-primary bg-primary/10 text-primary-text"
              : "-ml-px border-border text-muted-foreground hover:text-foreground"
          }`}
        >
          {view.label}
        </button>
      ))}
    </fieldset>
  );
}

export function AgenticPills({ items }: { items: readonly string[] }) {
  return (
    <span className="flex flex-wrap gap-1.5">
      {items.map((item) => (
        <code key={item} className="rounded-sm border border-border px-1.5 py-0.5 font-mono text-xs">
          {item}
        </code>
      ))}
    </span>
  );
}
