"use client";

import { X } from "lucide-react";
import dynamic from "next/dynamic";
import { useState } from "react";
import { ActionButton } from "../../components/action-button";
import { AgenticNodeList, AgenticPills, AgenticStatusBadge } from "../../components/agentic-parts";
import { ToggleGroup } from "../../components/toggle-group";
import type { AgenticNodeStatus } from "../../domain/agentic-progress";
import { useModalDialog } from "../../hooks/use-modal-dialog";
import {
  DISTILL_NODES,
  distillBoardCaption,
  distillNodeStatus,
  distillNodeViews,
} from "../domain/distill-progress";
import type { DistillRun } from "../entities";

const AgenticFlow = dynamic(
  () => import("../../components/agentic-flow").then((module) => module.AgenticFlow),
  {
    ssr: false,
    loading: () => (
      <p role="status" className="text-xs text-muted-foreground">
        Loading graph…
      </p>
    ),
  },
);

const MODES = [
  { id: "graph", label: "Graph" },
  { id: "list", label: "List" },
] as const;

/** Distill's Agents view uses the same graph/list and node-detail pattern as BD's other agent pipelines. */
export function DistillNodeBoard({
  status,
  progress,
}: {
  status: DistillRun["status"] | null;
  progress: DistillRun["progress"] | null;
}) {
  const [mode, setMode] = useState<(typeof MODES)[number]["id"]>("graph");
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);
  const views = distillNodeViews(status, progress);
  const selectedNode = DISTILL_NODES.find((node) => node.id === selectedNodeId);
  const caption = distillBoardCaption(status, progress);

  return (
    <>
      <section
        aria-label="Distill agent pipeline"
        className="min-w-0 rounded-md border border-border bg-card p-3"
      >
        <header className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <h3 className="text-sm font-medium">Distill pipeline</h3>
            <p aria-live="polite" className="mt-1 text-xs text-muted-foreground">
              {caption}
            </p>
          </div>
        </header>

        <div className="mt-3 grid min-w-0 grid-cols-1 gap-3 xl:grid-cols-[minmax(0,3fr)_minmax(16rem,1fr)]">
          <div className="min-w-0">
            {mode === "graph" ? (
              <AgenticFlow views={views} onOpenNode={setSelectedNodeId} ariaLabel="Distill pipeline graph" />
            ) : (
              <AgenticNodeList rows={views} onOpen={setSelectedNodeId} label="Distill pipeline nodes" />
            )}
          </div>
          <div className="flex min-w-0 flex-col">
            <div className="mb-3 flex min-w-0 flex-wrap items-center justify-end gap-2">
              <ToggleGroup
                legend="Distill pipeline shown as"
                options={MODES}
                value={mode}
                onChange={setMode}
              />
            </div>
            <section
              aria-label="Learning progress"
              className="relative min-w-0 rounded-sm border border-border bg-background xl:min-h-72 xl:flex-1"
            >
              <div className="flex min-h-0 flex-col p-3 xl:absolute xl:inset-0">
                <header className="flex flex-wrap items-baseline justify-between gap-2">
                  <h4 className="text-sm font-medium">Learning progress</h4>
                  {progress && progress.total_units > 0 ? (
                    <span className="text-xs tabular-nums text-muted-foreground">
                      {progress.completed_units} of {progress.total_units} units
                    </span>
                  ) : null}
                </header>
                {status === null ? (
                  <p className="mt-3 text-xs text-muted-foreground">
                    Unit and chapter progress appears here when a run starts.
                  </p>
                ) : progress ? (
                  <div className="mt-3 flex min-h-0 flex-col gap-4 xl:flex-1">
                    <ProgressMetric
                      label="Learning units"
                      current={progress.completed_units}
                      total={progress.total_units}
                    />
                    <ProgressMetric
                      label="Chapters"
                      current={progress.completed_chapters}
                      total={progress.total_chapters}
                    />
                  </div>
                ) : (
                  <p className="mt-3 text-xs text-muted-foreground">
                    This run has not reported progress totals yet.
                  </p>
                )}
              </div>
            </section>
          </div>
        </div>
      </section>

      {selectedNode ? (
        <DistillNodeSheet
          node={selectedNode}
          status={distillNodeStatus(selectedNode.id, status, progress)}
          runStatus={status}
          onClose={() => setSelectedNodeId(null)}
        />
      ) : null}
    </>
  );
}

function ProgressMetric({ label, current, total }: { label: string; current: number; total: number }) {
  const value = total > 0 ? Math.max(0, Math.min(current, total)) : 0;
  const percent = total > 0 ? Math.round((value / total) * 100) : null;
  return (
    <div>
      <div className="flex items-baseline justify-between gap-2 text-xs">
        <span className="text-muted-foreground">{label}</span>
        <span className="tabular-nums text-foreground">
          {total > 0 ? `${value.toLocaleString()} / ${total.toLocaleString()}` : "Not reported"}
        </span>
      </div>
      {percent !== null ? (
        <div
          role="progressbar"
          aria-label={label}
          aria-valuemin={0}
          aria-valuemax={total}
          aria-valuenow={value}
          aria-valuetext={`${value} of ${total}`}
          className="mt-2 h-1.5 overflow-hidden rounded-sm bg-muted"
        >
          <div className="h-full bg-primary" style={{ width: `${percent}%` }} />
        </div>
      ) : null}
    </div>
  );
}

function DistillNodeSheet({
  node,
  status,
  runStatus,
  onClose,
}: {
  node: (typeof DISTILL_NODES)[number];
  status: AgenticNodeStatus;
  runStatus: DistillRun["status"] | null;
  onClose: () => void;
}) {
  const dialog = useModalDialog();
  const detail =
    runStatus === null
      ? "This stage runs when you start Distill."
      : runStatus === "cancelled" && status === "pending"
        ? "The run stopped before this stage completed."
        : status === "done"
          ? "This stage completed successfully."
          : status === "running"
            ? "This stage is active. Its committed progress appears in the board as the run updates."
            : status === "failed"
              ? "This stage failed before completing. The run error is shown in the Distill progress panel."
              : runStatus === "queued"
                ? "This stage has not started; the run is queued for processing."
                : "This stage has not run yet.";

  return (
    <dialog
      ref={dialog}
      aria-labelledby="distill-node-title"
      onCancel={(event) => {
        event.preventDefault();
        onClose();
      }}
      className="m-0 ml-auto h-dvh max-h-none w-full max-w-2xl overflow-y-auto border-l border-border bg-background p-0 text-foreground backdrop:bg-background/80"
    >
      <header className="sticky top-0 z-10 flex items-start gap-3 border-b border-border bg-background px-5 py-4">
        <div className="min-w-0 flex-1">
          <h2 id="distill-node-title" className="text-lg font-semibold tracking-tight">
            {node.label}
          </h2>
          <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1">
            <AgenticStatusBadge status={status} />
            <span className="font-mono text-xs text-muted-foreground">distill · {node.id}</span>
          </div>
        </div>
        <ActionButton iconOnly onClick={onClose} aria-label="Close node details">
          <X aria-hidden />
        </ActionButton>
      </header>
      <div className="flex flex-col gap-5 px-5 py-4">
        <section aria-label="What this stage does" className="flex flex-col gap-3">
          <p className="text-sm">{node.detail}</p>
          <p className="text-sm text-muted-foreground">{detail}</p>
          <div className="grid grid-cols-[auto_1fr] items-baseline gap-x-3 gap-y-2 text-xs text-muted-foreground">
            <span>Reads</span>
            <AgenticPills items={node.reads} />
            <span>Writes</span>
            <AgenticPills items={node.writes} />
          </div>
        </section>
      </div>
    </dialog>
  );
}
