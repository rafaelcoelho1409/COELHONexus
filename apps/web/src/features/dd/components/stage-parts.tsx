"use client";

/** Presentation-only parts shared by the Docs Distiller stage views. */

import {
  Ban,
  BookOpen,
  ChartNoAxesColumn,
  Check,
  Circle,
  CircleDot,
  Clock3,
  Lock,
  Play,
  Trash2,
  X,
} from "lucide-react";
import Link from "next/link";
import type { ReactNode } from "react";

import type { ChapterCell } from "../domain/chapters";
import { fmtMs } from "../domain/format";
import type { NodeStatus, NodeView, StagePillState } from "../domain/stages";
import { ActionButton } from "./action-button";
import { SuccessMark } from "./success-mark";

const PILL_LABEL: Record<StagePillState["status"], string> = {
  idle: "Idle",
  working: "Working",
  done: "Done",
  needs_review: "Needs review",
  failed: "Failed",
  cancelled: "Cancelled",
};

const PILL_TONE: Record<StagePillState["status"], string> = {
  idle: "text-muted-foreground",
  working: "text-running-text",
  done: "text-success-text",
  needs_review: "text-warning-text",
  failed: "text-destructive-text",
  cancelled: "text-muted-foreground",
};

function StagePill({ pill, elapsedMs }: { pill: StagePillState; elapsedMs?: number | null }) {
  return (
    <span
      role="status"
      data-status={pill.status}
      className={`inline-flex items-center gap-1.5 text-xs ${PILL_TONE[pill.status]}`}
    >
      <span className="font-medium">{pill.label ?? PILL_LABEL[pill.status]}</span>
      {elapsedMs != null && elapsedMs > 0 ? (
        <span className="font-mono text-xs text-muted-foreground">{fmtMs(elapsedMs)}</span>
      ) : null}
    </span>
  );
}

const GLYPH_CLASS = "size-3 shrink-0";

export function StatusGlyph({ status }: { status: NodeStatus }) {
  if (status === "done") return <Check aria-hidden className={`${GLYPH_CLASS} text-success-text`} />;
  if (status === "running") {
    return <CircleDot aria-hidden className={`${GLYPH_CLASS} animate-pulse text-running-text`} />;
  }
  if (status === "needs_review")
    return <CircleDot aria-hidden className={`${GLYPH_CLASS} text-warning-text`} />;
  if (status === "future") return <Lock aria-hidden className={`${GLYPH_CLASS} text-muted-foreground`} />;
  if (status === "failed") return <X aria-hidden className={`${GLYPH_CLASS} text-destructive-text`} />;
  if (status === "stopped") return <Ban aria-hidden className={`${GLYPH_CLASS} text-muted-foreground`} />;
  return <Circle aria-hidden className={`${GLYPH_CLASS} text-muted-foreground`} />;
}

export const NODE_STATUS_LABEL: Record<NodeStatus, string> = {
  done: "done",
  running: "running",
  pending: "pending",
  future: "not available yet",
  needs_review: "needs review",
  failed: "failed",
  stopped: "stopped",
};

/** Glyph plus the status in words, so the state never rests on the icon or its colour alone. */
export function NodeStatusBadge({ status }: { status: NodeStatus }) {
  return (
    <span className="inline-flex items-center gap-1.5 text-xs text-muted-foreground">
      <StatusGlyph status={status} />
      {NODE_STATUS_LABEL[status]}
    </span>
  );
}

export type StageNodeDetails = {
  agents: readonly string[];
  skills: readonly string[];
  tools: readonly string[];
};

const NODE_DETAIL_ROWS = [
  { label: "Agent", key: "agents" },
  { label: "Skills", key: "skills" },
  { label: "Tools", key: "tools" },
] as const;

/** Agent, skill, and tool names shown consistently on graph and list nodes. */
export function NodeDetailsList({
  details,
  compact = false,
}: {
  details: StageNodeDetails;
  compact?: boolean;
}) {
  return (
    <span className={`grid min-w-0 gap-y-0.5 text-xs leading-4 ${compact ? "px-3 pt-1" : "mt-2"}`}>
      {NODE_DETAIL_ROWS.map(({ label, key }) => {
        const value = details[key].join(", ") || "None";
        return (
          <span key={key} className="grid min-w-0 grid-cols-[3.5rem_minmax(0,1fr)] gap-2">
            <span className="text-muted-foreground">{label}</span>
            <span title={value} className={`min-w-0 ${compact ? "truncate" : "break-words"}`}>
              {value}
            </span>
          </span>
        );
      })}
    </span>
  );
}

/** One card per graph node; it opens that node's drawer. */
export function StageNodes({
  views,
  liveLines,
  kpis,
  nodeDetails = {},
  onOpen,
  label = "Stage nodes",
}: {
  views: readonly NodeView[];
  liveLines: Record<string, string>;
  /** A node's headline number once it has committed ("1,200 files"). */
  kpis: Record<string, string>;
  /** Agent, skills, and tools associated with each node. */
  nodeDetails?: Readonly<Record<string, StageNodeDetails>>;
  onOpen: (nodeId: string) => void;
  /** What the list is called to assistive technology. */
  label?: string;
}) {
  return (
    // One node per row, top to bottom in run order — the same order as the graph.
    <ul aria-label={label} className="flex flex-col gap-1.5">
      {views.map((view) => {
        const details = nodeDetails[view.node.id];
        return (
          <li key={view.node.id} data-status={view.status} data-node={view.node.id}>
            <button
              type="button"
              onClick={() => onOpen(view.node.id)}
              aria-haspopup="dialog"
              className={`block min-h-9 w-full rounded-sm border bg-card px-3 py-2 text-left hover:bg-muted focus-visible:outline-2 focus-visible:outline-ring ${
                view.status === "running" ? "border-running" : "border-border"
              }`}
            >
              <span className="flex items-center justify-between gap-3">
                <span className="flex min-w-0 items-center gap-1.5">
                  <StatusGlyph status={view.status} />
                  <span className="text-sm">{view.node.label}</span>
                  <span className="sr-only">— {NODE_STATUS_LABEL[view.status]}</span>
                </span>
                {kpis[view.node.id] ? (
                  <span className="min-w-0 truncate text-xs text-muted-foreground tabular-nums">
                    {kpis[view.node.id]}
                  </span>
                ) : null}
              </span>
              {liveLines[view.node.id] ? (
                <span className="mt-1 block font-mono text-xs break-all text-muted-foreground">
                  {liveLines[view.node.id]}
                </span>
              ) : null}
              {details ? <NodeDetailsList details={details} /> : null}
            </button>
          </li>
        );
      })}
    </ul>
  );
}

/** A chapter's state as the same glyphs the nodes use: a check once written, not an empty ring. */
const CELL_GLYPH: Record<ChapterCell["status"], NodeStatus> = {
  pending: "pending",
  running: "running",
  done: "done",
  needs_review: "needs_review",
  failed: "failed",
  cancelled: "stopped",
};

const ICON_BUTTON =
  "inline-flex size-9 shrink-0 items-center justify-center rounded-sm text-muted-foreground hover:text-foreground disabled:opacity-40";

export function ChapterStrip({
  cells,
  counter,
  runEnabled,
  runTitle,
  studyHref,
  onRun,
  onWipe,
  onSelect,
}: {
  cells: readonly ChapterCell[];
  counter: string;
  runEnabled: boolean;
  runTitle: string;
  studyHref: (chapterId: string) => string;
  onRun: (id: string) => void;
  onWipe: (id: string) => void;
  onSelect: (id: string) => void;
}) {
  if (cells.length === 0) {
    return (
      <p data-testid="chstrip-empty" className="text-sm text-muted-foreground">
        The chapter list appears once the planner has written a plan.
      </p>
    );
  }
  return (
    <section aria-label="Chapters" className="flex min-h-0 flex-col rounded-md border border-border bg-card">
      <div className="flex items-center justify-between border-b border-border px-3 py-2">
        <span className="text-xs font-medium text-muted-foreground">Chapters</span>
        <span data-testid="chstrip-counter" className="font-mono text-xs text-muted-foreground">
          {counter}
        </span>
      </div>
      {/* Beside the graphs the list scrolls inside its column, so a long plan never makes the page taller than the graphs. */}
      <ul
        // biome-ignore lint/a11y/noNoninteractiveTabindex: the list scrolls beside the graphs, so keyboard users must be able to focus it to scroll it (WCAG 2.1.1)
        tabIndex={0}
        className="min-h-0 lg:overflow-y-auto"
      >
        {cells.map((cell, index) => (
          <li
            key={cell.id}
            data-chapter-id={cell.id}
            data-status={cell.status}
            className={`relative border-b border-border last:border-b-0 ${
              cell.selected ? "bg-muted" : cell.status === "done" && cell.threadId ? "hover:bg-muted" : ""
            }`}
          >
            {/* Two rows, as in the original strip: the whole title (it wraps, never clipped) on top; below it the time on
                the left and this chapter's own actions on the right. The title button stretches over the whole rectangle
                (its `after` covers the row), so a click anywhere on it picks the chapter; the action buttons sit above it. */}
            <div className="flex flex-col px-3">
              <div className="flex items-start gap-2">
                <span className="flex min-h-9 items-center">
                  <StatusGlyph status={CELL_GLYPH[cell.status]} />
                </span>
                <span className="sr-only">{cell.status}:</span>
                <button
                  type="button"
                  onClick={() => onSelect(cell.id)}
                  disabled={cell.status !== "done" || !cell.threadId}
                  className="min-h-9 min-w-0 flex-1 py-1.5 text-left text-sm break-words outline-none after:absolute after:inset-0 hover:text-foreground focus-visible:after:ring-2 focus-visible:after:ring-ring focus-visible:after:ring-inset enabled:cursor-pointer disabled:cursor-default"
                  title={cell.title}
                >
                  <span className="mr-1.5 font-mono text-xs text-muted-foreground">{index + 1}</span>
                  {cell.title}
                </button>
              </div>
              <div className="flex items-center justify-between gap-2 pl-6">
                <span
                  className={`font-mono text-xs ${
                    cell.status === "needs_review"
                      ? "text-warning-text"
                      : cell.status === "failed"
                        ? "text-destructive-text"
                        : "text-muted-foreground"
                  }`}
                >
                  {cell.synthEligible === false
                    ? "Excluded from Synth"
                    : cell.status === "needs_review"
                      ? "Needs review"
                      : cell.status === "failed"
                        ? "Failed"
                        : cell.status === "cancelled"
                          ? "Cancelled"
                          : fmtMs(cell.wallMs)}
                </span>
                <span className="relative z-10 flex items-center">
                  <button
                    type="button"
                    aria-label={`Run only ${cell.title}`}
                    data-testid={`chapter-run-${cell.id}`}
                    onClick={() => onRun(cell.id)}
                    disabled={!runEnabled || cell.synthEligible === false}
                    title={
                      cell.synthEligible === false
                        ? cell.exclusionReason || "Reference-only chapter"
                        : runTitle || "Run only this chapter"
                    }
                    className={ICON_BUTTON}
                  >
                    <Play className="size-4" aria-hidden />
                  </button>
                  <button
                    type="button"
                    aria-label={`Wipe only ${cell.title}`}
                    onClick={() => onWipe(cell.id)}
                    disabled={cell.status === "running"}
                    className={ICON_BUTTON}
                  >
                    <Trash2 className="size-4" aria-hidden />
                  </button>
                  {cell.status === "done" ? (
                    <Link
                      href={studyHref(cell.id)}
                      aria-label={`Open ${cell.title} in the study reader`}
                      className={ICON_BUTTON}
                    >
                      <BookOpen className="size-4" aria-hidden />
                    </Link>
                  ) : (
                    <span
                      className={`${ICON_BUTTON} opacity-40`}
                      title="Synthesize this chapter first"
                      role="img"
                      aria-label={`${cell.title} is not synthesized yet`}
                    >
                      <BookOpen className="size-4" aria-hidden />
                    </span>
                  )}
                </span>
              </div>
              {cell.synthEligible === false && cell.exclusionReason ? (
                <p className="ml-6 pb-2 text-xs text-muted-foreground">{cell.exclusionReason}</p>
              ) : null}
              {(cell.sourceFiles?.length ?? 0) > 0 ? (
                <details className="relative z-10 ml-6 pb-2">
                  <summary className="min-h-9 cursor-pointer py-2 text-xs text-muted-foreground">
                    {cell.sourceFiles?.length} source {cell.sourceFiles?.length === 1 ? "file" : "files"}
                  </summary>
                  <ul className="list-disc space-y-1 break-all pl-5 font-mono text-xs text-muted-foreground">
                    {cell.sourceFiles?.map((file) => (
                      <li key={file}>{file}</li>
                    ))}
                  </ul>
                </details>
              ) : null}
            </div>
          </li>
        ))}
      </ul>
    </section>
  );
}

/** The state and actions every run bar needs; both run controllers satisfy it. */
export type RunControls = {
  running: boolean;
  starting: boolean;
  canceling: boolean;
  pill: StagePillState;
  elapsedMs: number | null;
  startLabel: "Start" | "Resume" | "Run again";
  start: () => Promise<void>;
  cancel: () => Promise<void>;
  wipe: () => Promise<void>;
};

/** Pipeline's standalone stage-progress card: the same hierarchy as Ingestion progress, with chapter/node units. */
export function PipelineStageProgress({
  title,
  testId,
  controls,
  blockedMessage,
  error,
  onOpenUsage,
  current,
  total,
  valueText,
  summary,
  indeterminate = false,
  children,
}: {
  title: string;
  testId: string;
  controls: RunControls;
  blockedMessage: string | null;
  error: string | null;
  onOpenUsage: () => void;
  current: number;
  total: number;
  valueText: string;
  summary: string;
  indeterminate?: boolean;
  children?: ReactNode;
}) {
  const hasTotal = total > 0;
  const boundedCurrent = hasTotal ? Math.max(0, Math.min(current, total)) : 0;
  const percent = hasTotal ? Math.round((boundedCurrent / total) * 100) : null;
  const busy = controls.running || controls.starting;

  return (
    <section aria-label={`${title} progress`} className="min-w-0 rounded-md border border-border bg-card p-3">
      <div className="flex min-h-9 flex-wrap items-center justify-between gap-2">
        <h3 className="flex items-center gap-1.5 text-sm font-medium">
          {title}
          {controls.pill.status === "done" ? <SuccessMark /> : null}
        </h3>
        <div className="flex flex-wrap items-center justify-end gap-2">
          <StagePill pill={controls.pill} elapsedMs={controls.elapsedMs} />
          <div className="flex flex-wrap items-center justify-end gap-1.5">
            <ActionButton
              onClick={onOpenUsage}
              data-testid={`${testId}-usage`}
              aria-haspopup="dialog"
              className="px-2"
            >
              <ChartNoAxesColumn aria-hidden />
              LLM usage
            </ActionButton>
            <ActionButton
              onClick={() => void controls.start()}
              disabled={busy || blockedMessage !== null}
              aria-busy={controls.starting}
              data-testid={`${testId}-start`}
              className="px-2"
            >
              {controls.starting ? "Starting…" : controls.startLabel}
            </ActionButton>
            <ActionButton
              onClick={() => void controls.cancel()}
              disabled={!controls.running || controls.canceling}
              aria-busy={controls.canceling}
              data-testid={`${testId}-stop`}
              className="px-2"
            >
              {controls.canceling ? "Stopping…" : "Stop"}
            </ActionButton>
            <ActionButton
              onClick={() => void controls.wipe()}
              disabled={busy || blockedMessage !== null}
              data-testid={`${testId}-wipe`}
              className="px-2"
            >
              Wipe
            </ActionButton>
          </div>
        </div>
      </div>
      <div
        role="progressbar"
        aria-label={`${title} progress`}
        aria-valuemin={0}
        aria-valuemax={hasTotal ? total : 100}
        aria-valuenow={boundedCurrent}
        aria-valuetext={valueText}
        className="mt-2 h-1.5 w-full overflow-hidden rounded-sm bg-muted"
      >
        <div
          className={`h-full bg-primary ${indeterminate && !hasTotal ? "w-1/3 animate-pulse" : ""}`}
          style={percent === null ? (indeterminate ? undefined : { width: 0 }) : { width: `${percent}%` }}
        />
      </div>
      <p className="mt-1.5 flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-muted-foreground">
        <span className="tabular-nums">
          {summary}
          {percent !== null ? ` · ${percent}%` : ""}
        </span>
      </p>
      {children ? (
        <div className="mt-1 flex flex-wrap items-center gap-x-4 gap-y-1 empty:hidden">{children}</div>
      ) : null}
      {blockedMessage ? (
        <p role="status" className="mt-1 flex items-center gap-1.5 text-xs text-muted-foreground">
          <Clock3 className="size-4 shrink-0" aria-hidden />
          {blockedMessage}
        </p>
      ) : null}
      {error ? (
        <p role="alert" className="mt-1 break-words text-xs text-destructive-text">
          {error}
        </p>
      ) : null}
    </section>
  );
}

/** A stage's run controls, drawn in Ingestion's button style: title and status on top; below, LLM usage on the left and
 * Start / Stop / Wipe on the right. In Planner and Synth this sits inside the same outer card as the graph. `testId`
 * prefixes the button test ids. `children` sit under the title row (a health or audit line). */
export function RunBar({
  title,
  testId,
  controls,
  blockedMessage,
  onOpenUsage,
  children,
  embedded = false,
}: {
  title: string;
  testId: string;
  controls: RunControls;
  /** Non-null while a run elsewhere keeps this stage from starting. */
  blockedMessage: string | null;
  /** Opens the LLM usage sheet of this stage. */
  onOpenUsage: () => void;
  children?: ReactNode;
  /** Draw the controls inside their stage's shared card with the graph below. */
  embedded?: boolean;
}) {
  const busy = controls.running || controls.starting;
  return (
    // `justify-between`: when a neighbouring box is taller (the pipeline page gives both one height), the buttons stay at the bottom edge.
    <section
      aria-label={`${title} run`}
      className={`flex min-w-0 flex-col justify-between gap-2 ${
        embedded ? "" : "rounded-md border border-border bg-card p-3"
      }`}
    >
      <div>
        <div className="flex min-h-9 flex-wrap items-center justify-between gap-2">
          <h3 className="flex items-center gap-1.5 text-sm font-medium">
            {title}
            {controls.pill.status === "done" ? <SuccessMark /> : null}
          </h3>
          <StagePill pill={controls.pill} elapsedMs={controls.elapsedMs} />
        </div>
        <div className="mt-1 flex flex-wrap items-center gap-x-4 gap-y-1 empty:hidden">{children}</div>
        {blockedMessage ? (
          <p className="mt-1 flex items-center gap-1.5 text-xs text-muted-foreground">
            <Clock3 className="size-4 shrink-0" aria-hidden />
            {blockedMessage}
          </p>
        ) : null}
      </div>
      {/* Tight padding and gaps: in the narrowest column beside the graphs (320px) all four fit on one row. */}
      <div className="flex flex-wrap items-center gap-1.5">
        <ActionButton
          className="px-2"
          data-testid={`${testId}-usage`}
          onClick={onOpenUsage}
          aria-haspopup="dialog"
        >
          <ChartNoAxesColumn aria-hidden />
          LLM usage
        </ActionButton>
        <div className="ml-auto flex items-center gap-1.5">
          <ActionButton
            className="px-2"
            data-testid={`${testId}-start`}
            onClick={() => void controls.start()}
            disabled={busy || blockedMessage !== null}
            aria-busy={controls.starting}
          >
            {controls.startLabel}
          </ActionButton>
          <ActionButton
            className="px-2"
            data-testid={`${testId}-stop`}
            onClick={() => void controls.cancel()}
            disabled={!controls.running || controls.canceling}
            aria-busy={controls.canceling}
          >
            {controls.canceling ? "Stopping…" : "Stop"}
          </ActionButton>
          <ActionButton
            className="px-2"
            data-testid={`${testId}-wipe`}
            onClick={() => void controls.wipe()}
            disabled={busy || blockedMessage !== null}
          >
            Wipe
          </ActionButton>
        </div>
      </div>
    </section>
  );
}

export function NoFrameworkCard() {
  return (
    <div className="rounded-md border border-border bg-card p-8 text-center">
      <h2 className="text-base font-medium">No framework selected</h2>
      <p className="mt-1 text-sm text-muted-foreground">Pick a framework from the library picker above.</p>
    </div>
  );
}
