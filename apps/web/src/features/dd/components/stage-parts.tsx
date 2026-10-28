"use client";

/** Presentation-only parts shared by the Docs Distiller stage views. */

import { BookOpen, Check, Circle, CircleDot, Clock3, Lock, Play, Trash2, X } from "lucide-react";
import Link from "next/link";
import type { ReactNode } from "react";

import { Button } from "@/shared/ui/button";

import type { ChapterCell } from "../domain/chapters";
import { fmtMs } from "../domain/format";
import type { NodeStatus, StageNodeView, StagePillState } from "../domain/stages";

const PILL_LABEL: Record<StagePillState["status"], string> = {
  idle: "Idle",
  working: "Working",
  done: "Done",
  failed: "Failed",
  cancelled: "Cancelled",
};

const PILL_TONE: Record<StagePillState["status"], string> = {
  idle: "text-muted-foreground",
  working: "text-running-text",
  done: "text-success-text",
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
  if (status === "future") return <Lock aria-hidden className={`${GLYPH_CLASS} text-muted-foreground`} />;
  if (status === "failed") return <X aria-hidden className={`${GLYPH_CLASS} text-destructive-text`} />;
  return <Circle aria-hidden className={`${GLYPH_CLASS} text-muted-foreground`} />;
}

export const NODE_STATUS_LABEL: Record<NodeStatus, string> = {
  done: "done",
  running: "running",
  pending: "pending",
  future: "not available yet",
  failed: "failed",
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

/** One card per graph node; it opens that node's drawer. */
export function StageNodes({
  views,
  liveLines,
  kpis,
  onOpen,
}: {
  views: StageNodeView[];
  liveLines: Record<string, string>;
  /** A node's headline number once it has committed ("1,200 files"). */
  kpis: Record<string, string>;
  onOpen: (nodeId: string) => void;
}) {
  return (
    <ul aria-label="Stage nodes" className="grid grid-cols-1 gap-1.5 sm:grid-cols-2 lg:grid-cols-4">
      {views.map((view) => (
        <li key={view.node.id} data-status={view.status} data-node={view.node.id}>
          <button
            type="button"
            onClick={() => onOpen(view.node.id)}
            aria-haspopup="dialog"
            className={`block min-h-9 w-full rounded-sm border bg-card px-2.5 py-2 text-left hover:bg-muted ${
              view.status === "running" ? "border-running" : "border-border"
            }`}
          >
            <span className="flex items-center gap-1.5">
              <StatusGlyph status={view.status} />
              <span className="text-sm">{view.node.label}</span>
              <span className="sr-only">— {NODE_STATUS_LABEL[view.status]}</span>
            </span>
            {kpis[view.node.id] ? (
              <span className="mt-0.5 block text-xs text-muted-foreground">{kpis[view.node.id]}</span>
            ) : null}
            {liveLines[view.node.id] ? (
              <span className="mt-1 block font-mono text-xs break-all text-muted-foreground">
                {liveLines[view.node.id]}
              </span>
            ) : null}
          </button>
        </li>
      ))}
    </ul>
  );
}

/** A chapter's state as the same glyphs the nodes use: a check once written, not an empty ring. */
const CELL_GLYPH: Record<ChapterCell["status"], NodeStatus> = {
  pending: "pending",
  running: "running",
  done: "done",
  failed: "failed",
  cancelled: "failed",
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
    <section aria-label="Chapters" className="rounded-md border border-border bg-card">
      <div className="flex items-center justify-between border-b border-border px-3 py-2">
        <span className="text-xs font-medium text-muted-foreground">Chapters</span>
        <span data-testid="chstrip-counter" className="font-mono text-xs text-muted-foreground">
          {counter}
        </span>
      </div>
      <ul>
        {cells.map((cell, index) => (
          <li
            key={cell.id}
            data-chapter-id={cell.id}
            data-status={cell.status}
            className={`border-b border-border last:border-b-0 ${cell.selected ? "bg-muted" : ""}`}
          >
            {/* Two rows, as in the original strip: the whole title (it wraps, never clipped) on top; below it the time on
                the left and this chapter's own actions on the right. */}
            <div className="flex flex-col gap-0.5 px-3 py-1.5">
              <div className="flex items-start gap-2">
                <span className="flex min-h-9 items-center">
                  <StatusGlyph status={CELL_GLYPH[cell.status]} />
                </span>
                <span className="sr-only">{cell.status}:</span>
                <button
                  type="button"
                  onClick={() => onSelect(cell.id)}
                  disabled={cell.status !== "done" || !cell.threadId}
                  className="min-h-9 min-w-0 flex-1 py-1.5 text-left text-sm break-words hover:text-foreground disabled:cursor-default"
                  title={cell.title}
                >
                  <span className="mr-1.5 font-mono text-xs text-muted-foreground">{index + 1}</span>
                  {cell.title}
                </button>
              </div>
              <div className="flex items-center justify-between gap-2 pl-6">
                <span className="font-mono text-xs text-muted-foreground">{fmtMs(cell.wallMs)}</span>
                <span className="flex items-center">
                  <button
                    type="button"
                    aria-label={`Run only ${cell.title}`}
                    data-testid={`chapter-run-${cell.id}`}
                    onClick={() => onRun(cell.id)}
                    disabled={!runEnabled}
                    title={runTitle || "Run only this chapter"}
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
  startLabel: "Start" | "Resume";
  start: () => Promise<void>;
  cancel: () => Promise<void>;
  wipe: () => Promise<void>;
};

/** Title, status, and Start / Stop / Wipe for one stage. `testId` prefixes the button test ids. */
export function RunBar({
  title,
  testId,
  controls,
  blockedMessage,
  onOpenUsage,
  children,
}: {
  title: string;
  testId: string;
  controls: RunControls;
  /** Non-null while a run elsewhere keeps this stage from starting. */
  blockedMessage: string | null;
  /** Opens the LLM usage sheet of this stage. */
  onOpenUsage: () => void;
  children?: ReactNode;
}) {
  const busy = controls.running || controls.starting;
  return (
    <header className="flex flex-wrap items-center gap-x-4 gap-y-2 rounded-md border border-border bg-card px-3 py-2.5">
      <h2 className="text-sm font-medium">{title}</h2>
      <StagePill pill={controls.pill} elapsedMs={controls.elapsedMs} />
      {children}
      {blockedMessage ? (
        <p className="flex items-center gap-1.5 text-xs text-muted-foreground">
          <Clock3 className="size-4 shrink-0" aria-hidden />
          {blockedMessage}
        </p>
      ) : null}
      <div className="ml-auto flex flex-wrap items-center gap-2">
        <Button
          size="lg"
          variant="ghost"
          data-testid={`${testId}-usage`}
          onClick={onOpenUsage}
          aria-haspopup="dialog"
        >
          LLM usage
        </Button>
        <Button
          size="lg"
          data-testid={`${testId}-start`}
          onClick={() => void controls.start()}
          disabled={busy || blockedMessage !== null}
          aria-busy={controls.starting}
        >
          {controls.startLabel}
        </Button>
        <Button
          size="lg"
          variant="outline"
          data-testid={`${testId}-stop`}
          onClick={() => void controls.cancel()}
          disabled={!controls.running || controls.canceling}
          aria-busy={controls.canceling}
        >
          {controls.canceling ? "Stopping…" : "Stop"}
        </Button>
        <Button
          size="lg"
          variant="destructive"
          data-testid={`${testId}-wipe`}
          onClick={() => void controls.wipe()}
          disabled={busy}
        >
          Wipe
        </Button>
      </div>
    </header>
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
