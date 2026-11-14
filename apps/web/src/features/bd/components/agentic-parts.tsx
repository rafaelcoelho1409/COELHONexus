"use client";

/** Parts shared by the Agentic AI graph, its list and its node sheet. They draw a node the way the Docs Distiller's
 * stage graphs do: a glyph for the state, the state also in words, a headline number once the node is done and a
 * monospace line while it runs. */

import { Check, Circle, CircleDot, X } from "lucide-react";

import type {
  AgenticNodeProgress,
  AgenticNodeResources,
  AgenticNodeStatus,
  AgenticNodeView,
} from "../domain/agentic-progress";

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
  label = "Agentic pipeline nodes",
}: {
  rows: readonly AgenticNodeView[];
  onOpen: (nodeId: string) => void;
  label?: string;
}) {
  return (
    <ol aria-label={label} className="flex flex-col gap-1.5">
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
            <span className="mt-0.5 block font-mono text-xs text-muted-foreground">{row.id}</span>
            {row.liveLine ? (
              <span className="mt-1 block font-mono text-xs break-all text-muted-foreground">
                {row.liveLine}
              </span>
            ) : null}
            <AgenticNodeDetailsList resources={row.resources} />
            {row.progress ? <AgenticNodeProgressBar label={row.label} progress={row.progress} /> : null}
          </button>
        </li>
      ))}
    </ol>
  );
}

const RESOURCE_ROWS = [
  { label: "Agent", key: "agents" },
  { label: "Skills", key: "skills" },
  { label: "Tools", key: "tools" },
] as const;

export function AgenticNodeDetailsList({
  resources,
  compact = false,
}: {
  resources: AgenticNodeResources;
  compact?: boolean;
}) {
  return (
    <span className={`grid min-w-0 gap-y-0.5 text-xs leading-4 ${compact ? "" : "mt-1"}`}>
      {RESOURCE_ROWS.map(({ label, key }) => {
        const items = resources[key];
        return (
          <span key={key} className="grid min-w-0 grid-cols-[3.5rem_minmax(0,1fr)] gap-2">
            <span className="text-muted-foreground">{label}</span>
            <span className="flex min-w-0 flex-col">
              {items.length > 0 ? (
                items.map((item) => (
                  <span key={item} title={item} className={`min-w-0 ${compact ? "truncate" : "break-words"}`}>
                    {item}
                  </span>
                ))
              ) : (
                <span>None</span>
              )}
            </span>
          </span>
        );
      })}
    </span>
  );
}

export function AgenticNodeProgressBar({
  label,
  progress,
}: {
  label: string;
  progress: AgenticNodeProgress;
}) {
  const current = Math.max(0, Math.min(progress.current, progress.total));
  const percent = progress.total > 0 ? Math.round((current / progress.total) * 100) : 0;
  const active = progress.active > 0 ? ` · ${progress.active} active` : "";
  return (
    <span className="mt-1 block">
      <span className="flex items-center justify-between gap-2 text-xs">
        <span className="min-w-0 truncate text-muted-foreground">{progress.label}</span>
        <span className="shrink-0 tabular-nums">
          {current} / {progress.total}
          {active}
        </span>
      </span>
      <span
        role="progressbar"
        aria-label={`${label} ${progress.label}`}
        aria-valuemin={0}
        aria-valuemax={progress.total}
        aria-valuenow={current}
        aria-valuetext={`${current} of ${progress.total} ${progress.label}${active}`}
        className="mt-1 block h-1.5 w-full overflow-hidden rounded-sm bg-muted"
      >
        <span aria-hidden="true" className="block h-full bg-primary" style={{ width: `${percent}%` }} />
      </span>
    </span>
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
