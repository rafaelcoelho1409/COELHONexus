"use client";

/** Parts shared by the Agentic AI graph, its list and its node sheet. They draw a node consistently across views.
 * stage graphs do: a glyph for the state, the state also in words, a headline number once the node is done and a
 * monospace line while it runs. */

import { Check, Circle, CircleDot, LoaderCircle, Play, X } from "lucide-react";

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
  if (status === "cancelled") return <Circle aria-hidden className={`${GLYPH_CLASS} text-warning-text`} />;
  return <Circle aria-hidden className={`${GLYPH_CLASS} text-muted-foreground`} />;
}

/** Shared replay control for graph and list views; pointer events never bubble into the node-details action. */
export function AgenticReplayButton({
  label,
  onPlay,
  pending = false,
  disabled = false,
  className = "",
}: {
  label: string;
  onPlay: () => void;
  pending?: boolean;
  disabled?: boolean;
  className?: string;
}) {
  return (
    <button
      type="button"
      onPointerDown={(event) => event.stopPropagation()}
      onMouseDown={(event) => event.stopPropagation()}
      onClick={(event) => {
        event.stopPropagation();
        onPlay();
      }}
      disabled={disabled || pending}
      aria-busy={pending}
      aria-label={pending ? `Queueing replay from ${label}` : `Replay from ${label}`}
      title={pending ? `Queueing replay from ${label}` : `Replay from ${label} and clear downstream results`}
      className={`nodrag nopan pointer-events-auto inline-flex size-9 shrink-0 items-center justify-center rounded-sm text-muted-foreground hover:bg-muted hover:text-foreground focus-visible:outline-2 focus-visible:outline-ring disabled:pointer-events-none disabled:opacity-60 ${className}`}
    >
      {pending ? (
        <LoaderCircle aria-hidden className="size-4 animate-spin" />
      ) : (
        <Play aria-hidden className="size-4" />
      )}
    </button>
  );
}

export const AGENTIC_STATUS_LABEL: Record<AgenticNodeStatus, string> = {
  done: "done",
  running: "running",
  pending: "pending",
  failed: "failed",
  cancelled: "cancelled",
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
  onPlay,
  replayDisabled = false,
  replayPending = false,
  replayPendingNodeId = null,
  label = "Agentic pipeline nodes",
}: {
  rows: readonly AgenticNodeView[];
  onOpen: (nodeId: string) => void;
  onPlay?: (nodeId: string) => void;
  replayDisabled?: boolean;
  replayPending?: boolean;
  replayPendingNodeId?: string | null;
  label?: string;
}) {
  return (
    <ol aria-label={label} className="flex flex-col gap-1.5">
      {rows.map((row) => (
        <li key={row.id} data-status={row.status} data-node={row.id}>
          <div
            className={`flex min-w-0 items-stretch gap-1.5 rounded-sm border bg-card p-1.5 ${
              row.status === "running"
                ? "border-running"
                : row.status === "failed"
                  ? "border-destructive"
                  : "border-border"
            }`}
          >
            <button
              type="button"
              onClick={() => onOpen(row.id)}
              aria-haspopup="dialog"
              aria-label={`Open ${row.label} node details`}
              className="min-h-9 min-w-0 flex-1 rounded-sm px-1.5 py-1 text-left hover:bg-muted focus-visible:outline-2 focus-visible:outline-ring"
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
              {row.progress ? <AgenticNodeProgressBar label={row.label} progress={row.progress} /> : null}
            </button>
            {onPlay ? (
              <AgenticReplayButton
                label={row.label}
                onPlay={() => onPlay(row.id)}
                pending={replayPending && replayPendingNodeId === row.id}
                disabled={replayDisabled || replayPending}
                className="self-start"
              />
            ) : null}
          </div>
        </li>
      ))}
    </ol>
  );
}

const RESOURCE_ROWS = [
  { label: "Agents", key: "agents" },
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
  const active = progress.activeLabel
    ? ` · ${progress.active} ${progress.activeLabel}`
    : progress.active > 0
      ? ` · ${progress.active} active`
      : "";
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
