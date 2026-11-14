"use client";

import { useQuery } from "@tanstack/react-query";
import { ChartNoAxesColumn, X } from "lucide-react";
import dynamic from "next/dynamic";
import { useState } from "react";
import { apiErrorMessage } from "@/shared/lib/api-error";
import { MermaidTopology } from "@/shared/markdown/mermaid-topology";
import { ActionButton } from "../../components/action-button";
import {
  AgenticNodeDetailsList,
  AgenticNodeList,
  AgenticPills,
  AgenticStatusBadge,
} from "../../components/agentic-parts";
import { ToggleGroup } from "../../components/toggle-group";
import type { AgenticNodeStatus } from "../../domain/agentic-progress";
import { useModalDialog } from "../../hooks/use-modal-dialog";
import { fetchPipelineUsage } from "../../rag/api";
import { PipelineUsageBreakdown, PipelineUsageSheet } from "../../rag/components/pipeline-usage-sheet";
import { SideSheet } from "../../rag/components/side-sheet";
import {
  DISTILL_LANGGRAPH_TOPOLOGY,
  DISTILL_NODES,
  DISTILL_USAGE_COUNTERS,
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
  { id: "langgraph", label: "LangGraph" },
] as const;

/** Distill's Agents view uses the same graph/list and node-detail pattern as BD's other agent pipelines. */
export function DistillNodeBoard({
  bookId,
  status,
  progress,
  statusText,
  runId,
  progressOpen,
  onProgressOpenChange,
}: {
  bookId: string;
  status: DistillRun["status"] | null;
  progress: DistillRun["progress"] | null;
  statusText: string;
  runId: string | null;
  progressOpen: boolean;
  onProgressOpenChange: (open: boolean) => void;
}) {
  const [mode, setMode] = useState<(typeof MODES)[number]["id"]>("graph");
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);
  const [usageOpen, setUsageOpen] = useState(false);
  const views = distillNodeViews(status, progress);
  const selectedNode = DISTILL_NODES.find((node) => node.id === selectedNodeId);
  const caption = distillBoardCaption(status, progress);

  return (
    <>
      <section
        aria-label="Distill agent pipeline"
        className="min-w-0 rounded-md border border-border bg-card p-3"
      >
        <header className="flex min-w-0 flex-col gap-2 sm:flex-row sm:items-start sm:justify-between">
          <div className="min-w-0">
            <h3 className="text-sm font-medium">Distill pipeline</h3>
            <p aria-live="polite" className="mt-1 text-xs text-muted-foreground">
              {caption}
            </p>
          </div>
          <div className="flex min-w-0 flex-col items-start gap-1 sm:items-end">
            <span aria-live="polite" className="text-xs text-muted-foreground">
              {statusText}
            </span>
          </div>
        </header>

        <div className="mt-3 flex min-w-0 flex-col gap-3">
          <div className="flex min-w-0 flex-col gap-2">
            <ActionButton
              className="w-full justify-center whitespace-nowrap px-2"
              onClick={() => setUsageOpen(true)}
              aria-haspopup="dialog"
            >
              <ChartNoAxesColumn aria-hidden />
              LLM usage
            </ActionButton>
            <ToggleGroup
              legend="Distill pipeline shown as"
              options={MODES}
              value={mode}
              onChange={setMode}
              fullWidth
            />
          </div>

          <div className="min-w-0 overflow-hidden rounded-sm border border-border bg-background">
            {mode === "graph" ? (
              <AgenticFlow views={views} onOpenNode={setSelectedNodeId} ariaLabel="Distill pipeline graph" />
            ) : mode === "list" ? (
              <div className="p-3">
                <AgenticNodeList rows={views} onOpen={setSelectedNodeId} label="Distill pipeline nodes" />
              </div>
            ) : (
              <MermaidTopology title="Distill pipeline" source={DISTILL_LANGGRAPH_TOPOLOGY} />
            )}
          </div>
        </div>
      </section>

      {selectedNode ? (
        <DistillNodeSheet
          bookId={bookId}
          node={selectedNode}
          runId={runId}
          status={distillNodeStatus(selectedNode.id, status, progress)}
          runStatus={status}
          onClose={() => setSelectedNodeId(null)}
        />
      ) : null}
      {progressOpen ? (
        <LearningProgressSheet
          status={status}
          statusText={statusText}
          progress={progress}
          onClose={() => onProgressOpenChange(false)}
        />
      ) : null}
      {usageOpen ? (
        <PipelineUsageSheet
          bookId={bookId}
          runId={runId}
          nodeIds={["learning_extractor", "evidence_verifier", "chapter_synthesizer", "book_synthesizer"]}
          live={status === "queued" || status === "running"}
          title="Distill LLM usage"
          subtitle="Calls and token counts recorded for this book run."
          onClose={() => setUsageOpen(false)}
        />
      ) : null}
    </>
  );
}

function LearningProgressSheet({
  status,
  statusText,
  progress,
  onClose,
}: {
  status: DistillRun["status"] | null;
  statusText: string;
  progress: DistillRun["progress"] | null;
  onClose: () => void;
}) {
  const dialog = useModalDialog();
  const totalUnits = progress?.total_units ?? 0;
  const completedUnits = progress ? Math.max(0, Math.min(progress.completed_units, totalUnits)) : 0;
  const completedPercent = totalUnits > 0 ? Math.round((completedUnits / totalUnits) * 100) : 0;

  return (
    <dialog
      ref={dialog}
      aria-labelledby="distill-progress-title"
      onCancel={(event) => {
        event.preventDefault();
        onClose();
      }}
      className="m-0 ml-auto h-dvh max-h-none w-full max-w-2xl overflow-y-auto border-l border-border bg-background p-0 text-foreground backdrop:bg-background/80"
    >
      <header className="sticky top-0 z-10 flex items-start gap-3 border-b border-border bg-background px-5 py-4">
        <div className="min-w-0 flex-1">
          <h2 id="distill-progress-title" className="text-lg font-semibold tracking-tight">
            Learning progress
          </h2>
          <p className="mt-1 text-xs text-muted-foreground">{statusText}</p>
        </div>
        <ActionButton iconOnly onClick={onClose} aria-label="Close learning progress">
          <X aria-hidden />
        </ActionButton>
      </header>
      <div className="flex flex-col gap-4 px-5 py-4">
        {status === null ? (
          <p className="text-sm text-muted-foreground">
            Unit and chapter progress appears here when a run starts.
          </p>
        ) : progress ? (
          <>
            <section className="rounded-sm border border-border bg-card p-4">
              <header className="flex flex-wrap items-baseline justify-between gap-2">
                <h3 className="text-sm font-medium">Learning units</h3>
                <span className="text-sm font-semibold tabular-nums">
                  {totalUnits > 0
                    ? `${completedUnits.toLocaleString()} / ${totalUnits.toLocaleString()}`
                    : "Not reported"}
                </span>
              </header>
              {totalUnits > 0 ? (
                <div
                  role="progressbar"
                  aria-label="Learning units completed"
                  aria-valuemin={0}
                  aria-valuemax={totalUnits}
                  aria-valuenow={completedUnits}
                  aria-valuetext={`${completedUnits} of ${totalUnits} learning units · ${completedPercent}%`}
                  className="mt-3 h-2 overflow-hidden rounded-sm bg-muted"
                >
                  <div className="h-full bg-primary" style={{ width: `${completedPercent}%` }} />
                </div>
              ) : null}
            </section>
            <section aria-label="Chapter progress" className="rounded-sm border border-border bg-card p-4">
              <ProgressMetric
                label="Chapters"
                current={progress.completed_chapters}
                total={progress.total_chapters}
              />
            </section>
          </>
        ) : (
          <p className="text-sm text-muted-foreground">This run has not reported progress totals yet.</p>
        )}
      </div>
    </dialog>
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
  bookId,
  node,
  runId,
  status,
  runStatus,
  onClose,
}: {
  bookId: string;
  node: (typeof DISTILL_NODES)[number];
  runId: string | null;
  status: AgenticNodeStatus;
  runStatus: DistillRun["status"] | null;
  onClose: () => void;
}) {
  const usageCounterIds = DISTILL_USAGE_COUNTERS[node.id] ?? [];
  const usageQuery = useQuery({
    queryKey: ["bd", "pipeline", "usage", bookId, runId] as const,
    queryFn: () => fetchPipelineUsage(bookId, runId ?? ""),
    enabled: runId !== null,
    refetchInterval: runStatus === "queued" || runStatus === "running" ? 3_000 : false,
    retry: false,
  });
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
    <SideSheet
      title={node.label}
      subtitle={
        <span className="flex flex-wrap items-center gap-x-3 gap-y-1">
          <AgenticStatusBadge status={status} />
          <span className="font-mono">distill · {node.id}</span>
        </span>
      }
      closeLabel="Close node details"
      onClose={onClose}
    >
      <div className="flex flex-col gap-5">
        <section aria-label="What this stage does" className="flex flex-col gap-3">
          <p className="text-sm">{node.detail}</p>
          <p className="text-sm text-muted-foreground">{detail}</p>
          <AgenticNodeDetailsList resources={node.resources} />
          <div className="grid grid-cols-[auto_1fr] items-baseline gap-x-3 gap-y-2 text-xs text-muted-foreground">
            <span>Reads</span>
            <AgenticPills items={node.reads} />
            <span>Writes</span>
            <AgenticPills items={node.writes} />
          </div>
        </section>
        <section aria-label="Node LLM usage" className="flex flex-col gap-3">
          <div>
            <h3 className="text-sm font-medium">LLM usage</h3>
            <p className="mt-1 text-xs text-muted-foreground">
              Calls and token counts recorded for this graph stage.
            </p>
          </div>
          <PipelineUsageBreakdown
            usage={usageQuery.data}
            nodeIds={usageCounterIds}
            hasRun={runId !== null}
            loading={usageQuery.isPending}
            failed={usageQuery.isError}
            errorMessage={usageQuery.isError ? apiErrorMessage(usageQuery.error) : null}
            onRetry={() => void usageQuery.refetch()}
            emptyMessage="Usage appears here when Distill has a run."
            noCallsMessage={
              usageCounterIds.length === 0
                ? "This stage does not make LLM calls."
                : "No LLM calls have been recorded for this stage yet."
            }
          />
        </section>
      </div>
    </SideSheet>
  );
}
