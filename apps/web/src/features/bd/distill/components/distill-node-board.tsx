"use client";

import { useQuery } from "@tanstack/react-query";
import { ChartNoAxesColumn, X } from "lucide-react";
import dynamic from "next/dynamic";
import { type ReactNode, useState } from "react";
import { apiErrorMessage } from "@/shared/lib/api-error";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/shared/ui/tabs";
import { ActionButton } from "../../components/action-button";
import {
  AgenticNodeDetailsList,
  AgenticNodeList,
  AgenticPills,
  AgenticReplayButton,
  AgenticStatusBadge,
} from "../../components/agentic-parts";
import { ToggleGroup } from "../../components/toggle-group";
import type { AgenticNodeStatus } from "../../domain/agentic-progress";
import { useModalDialog } from "../../hooks/use-modal-dialog";
import { fetchPipelineUsage } from "../../rag/api";
import { PipelineUsageBreakdown, PipelineUsageSheet } from "../../rag/components/pipeline-usage-sheet";
import { SideSheet } from "../../rag/components/side-sheet";
import type { DistillLangGraphNode } from "../domain/distill-progress";
import {
  DISTILL_CONNECTIONS,
  DISTILL_DISPATCH_USAGE_COUNTERS,
  DISTILL_LANGGRAPH_NODES,
  DISTILL_NODES,
  DISTILL_POSITIONS,
  DISTILL_REPORT_PIPELINE_CONNECTIONS,
  DISTILL_REPORT_PIPELINE_NODE_IDS,
  DISTILL_REPORT_PIPELINE_POSITIONS,
  DISTILL_REPORT_PIPELINE_USAGE_COUNTERS,
  DISTILL_USAGE_COUNTERS,
  distillBoardCaption,
  distillLangGraphNodeViews,
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
  bookId,
  status,
  progress,
  statusText,
  runId,
  processProgress,
  progressDetails,
  progressOpen,
  onProgressOpenChange,
  onPlayNode,
  replayDisabled,
  replayPending,
  replayPendingNodeId,
  replayMessage,
}: {
  bookId: string;
  status: DistillRun["status"] | null;
  progress: DistillRun["progress"] | null;
  statusText: string;
  runId: string | null;
  processProgress: ReactNode;
  progressDetails: ReactNode;
  progressOpen: boolean;
  onProgressOpenChange: (open: boolean) => void;
  onPlayNode?: (nodeId: string) => void;
  replayDisabled: boolean;
  replayPending: boolean;
  replayPendingNodeId: string | null;
  replayMessage: { kind: "status" | "error"; text: string } | null;
}) {
  const [mode, setMode] = useState<(typeof MODES)[number]["id"]>("graph");
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);
  const [usageOpen, setUsageOpen] = useState(false);
  const views = distillLangGraphNodeViews(status, progress);
  const progressViews = distillNodeViews(status, progress);
  const selectedGraphNode = DISTILL_LANGGRAPH_NODES.find((node) => node.id === selectedNodeId);
  const selectedStage = DISTILL_NODES.find((node) => node.id === selectedNodeId);
  const selectedView =
    views.find((view) => view.id === selectedNodeId) ??
    progressViews.find((view) => view.id === selectedNodeId);
  const caption = distillBoardCaption(status, progress);
  const playLangGraphNode = onPlayNode
    ? (nodeId: string) => onPlayNode(nodeId === "report_pipeline" ? "synthesize_book" : nodeId)
    : undefined;
  const topLevelReplayPendingNodeId =
    replayPendingNodeId === "synthesize_book" ? "report_pipeline" : replayPendingNodeId;

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
        {replayMessage ? (
          <p
            role={replayMessage.kind === "error" ? "alert" : "status"}
            className={`mt-3 break-words text-sm ${
              replayMessage.kind === "error" ? "text-destructive-text" : "text-muted-foreground"
            }`}
          >
            {replayMessage.text}
          </p>
        ) : null}

        <div className="mt-3 grid min-w-0 grid-cols-1 items-start gap-3 xl:grid-cols-[minmax(0,4fr)_minmax(0,1fr)]">
          <div className="min-w-0 overflow-hidden rounded-sm border border-border bg-background">
            {mode === "graph" ? (
              <AgenticFlow
                views={views}
                onOpenNode={setSelectedNodeId}
                onPlayNode={playLangGraphNode}
                replayDisabled={replayDisabled}
                replayPending={replayPending}
                replayPendingNodeId={topLevelReplayPendingNodeId}
                ariaLabel="Distill pipeline graph"
                connections={DISTILL_CONNECTIONS}
                positions={DISTILL_POSITIONS}
              />
            ) : mode === "list" ? (
              <div className="p-3">
                <AgenticNodeList
                  rows={views}
                  onOpen={setSelectedNodeId}
                  onPlay={playLangGraphNode}
                  replayDisabled={replayDisabled}
                  replayPending={replayPending}
                  replayPendingNodeId={topLevelReplayPendingNodeId}
                  label="Distill pipeline nodes"
                />
              </div>
            ) : null}
          </div>
          <div className="flex min-w-0 flex-col gap-3">
            {processProgress}
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
            {progressDetails}
          </div>
        </div>
      </section>

      {selectedGraphNode?.kind === "control" ? (
        <DistillControlNodeSheet
          node={selectedGraphNode}
          status={selectedView?.status ?? "pending"}
          runStatus={status}
          bookId={bookId}
          runId={runId}
          onPlayNode={onPlayNode}
          replayDisabled={replayDisabled}
          replayPending={replayPending}
          replayPendingNodeId={replayPendingNodeId}
          onClose={() => setSelectedNodeId(null)}
        />
      ) : selectedGraphNode?.kind === "subgraph" ? (
        <DistillReportPipelineSheet
          bookId={bookId}
          runId={runId}
          runStatus={status}
          progress={progress}
          status={selectedView?.status ?? "pending"}
          kpi={selectedView?.kpi ?? null}
          onClose={() => setSelectedNodeId(null)}
          onOpenNode={setSelectedNodeId}
          onPlayNode={onPlayNode}
          replayDisabled={replayDisabled}
          replayPending={replayPending}
          replayPendingNodeId={replayPendingNodeId}
        />
      ) : selectedStage ? (
        <DistillNodeSheet
          key={selectedStage.id}
          bookId={bookId}
          node={selectedStage}
          runId={runId}
          status={selectedView?.status ?? distillNodeStatus(selectedStage.id, status, progress)}
          runStatus={status}
          kpi={selectedView?.kpi ?? null}
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
          nodeIds={[
            "learning_extractor",
            "evidence_verifier",
            "chapter_synthesizer",
            "visual_artifact_builder",
            "book_synthesizer",
            "decision_brief_writer",
            "research_analyst",
            "report_reviewer",
            "chapter_coverage_corrector",
          ]}
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
              <h3 className="text-sm font-medium">Learning unit stages</h3>
              <div className="mt-3 flex flex-col gap-3">
                <ProgressMetric
                  label="Units extracted"
                  current={progress.completed_extractions}
                  total={totalUnits}
                />
                <ProgressMetric
                  label="Evidence checks complete"
                  current={progress.completed_units}
                  total={totalUnits}
                />
              </div>
            </section>
            <section
              aria-label="Chapter stage progress"
              className="rounded-sm border border-border bg-card p-4"
            >
              <div className="flex flex-col gap-3">
                <ProgressMetric
                  label="Chapter guides written"
                  current={progress.completed_chapter_guides}
                  total={progress.total_chapters}
                />
                <ProgressMetric
                  label="Visual stages complete"
                  current={progress.completed_chapter_visuals}
                  total={progress.total_chapters}
                />
                <ProgressMetric
                  label="Chapters ready"
                  current={progress.completed_chapters}
                  total={progress.total_chapters}
                />
              </div>
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
  kpi,
  onClose,
}: {
  bookId: string;
  node: (typeof DISTILL_NODES)[number];
  runId: string | null;
  status: AgenticNodeStatus;
  runStatus: DistillRun["status"] | null;
  kpi: string | null;
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
        : runStatus === "done" && status === "pending"
          ? "This stage was not part of the completed run path."
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
          {kpi ? <span className="font-medium tabular-nums">{kpi}</span> : null}
        </span>
      }
      closeLabel="Close node details"
      onClose={onClose}
    >
      <div className="flex flex-col gap-5">
        <Tabs defaultValue="overview" className="w-full">
          <TabsList aria-label="Node details">
            <TabsTrigger value="overview">Overview</TabsTrigger>
            <TabsTrigger value="llm-calls">LLM Calls</TabsTrigger>
            <TabsTrigger value="resources">Resources</TabsTrigger>
          </TabsList>
          <TabsContent value="overview" className="flex flex-col gap-3 pt-4">
            <p className="text-sm">{node.detail}</p>
            <p className="text-sm text-muted-foreground">{detail}</p>
            <div className="grid grid-cols-[auto_1fr] items-baseline gap-x-3 gap-y-2 text-xs text-muted-foreground">
              <span>Reads</span>
              <AgenticPills items={node.reads} />
              <span>Writes</span>
              <AgenticPills items={node.writes} />
              <span>LLM</span>
              <span className="text-sm text-foreground">{node.llm}</span>
            </div>
          </TabsContent>
          <TabsContent value="llm-calls" className="flex flex-col gap-3 pt-4">
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
                    ? "This deterministic stage does not make LLM calls."
                    : "No LLM calls were recorded for this stage. It may reuse saved unit results; otherwise, its configured agent call did not reach the usage counter."
                }
              />
            </section>
          </TabsContent>
          <TabsContent value="resources" className="pt-4">
            <section aria-label="Agents, skills, and tools">
              <AgenticNodeDetailsList resources={node.resources} />
            </section>
          </TabsContent>
        </Tabs>
      </div>
    </SideSheet>
  );
}

function DistillControlNodeSheet({
  node,
  status,
  runStatus,
  bookId,
  runId,
  onPlayNode,
  replayDisabled,
  replayPending,
  replayPendingNodeId,
  onClose,
}: {
  node: Extract<DistillLangGraphNode, { kind: "control" }>;
  status: AgenticNodeStatus;
  runStatus: DistillRun["status"] | null;
  bookId: string;
  runId: string | null;
  onPlayNode?: (nodeId: string) => void;
  replayDisabled: boolean;
  replayPending: boolean;
  replayPendingNodeId: string | null;
  onClose: () => void;
}) {
  const targets = DISTILL_CONNECTIONS.filter((connection) => connection.source === node.id).map(
    (connection) => DISTILL_LANGGRAPH_NODES.find((candidate) => candidate.id === connection.target)?.id,
  );

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
      <Tabs defaultValue="overview" className="w-full">
        <TabsList aria-label="Node details">
          <TabsTrigger value="overview">Overview</TabsTrigger>
          <TabsTrigger value="llm-calls">LLM Calls</TabsTrigger>
        </TabsList>
        <TabsContent value="overview" className="flex flex-col gap-4 pt-4">
          <p className="text-sm">
            {runStatus === "done" && status === "pending"
              ? "This graph control node was not reached by the completed run path."
              : node.detail}
          </p>
          <div className="flex items-center justify-between gap-3">
            <p className="text-xs text-muted-foreground">
              No direct LLM call. Usage is shown for the branches this node schedules.
            </p>
            {onPlayNode ? (
              <AgenticReplayButton
                label={node.label}
                onPlay={() => onPlayNode(node.id)}
                pending={replayPending && replayPendingNodeId === node.id}
                disabled={replayDisabled || replayPending}
              />
            ) : null}
          </div>
          <section className="rounded-sm border border-border bg-card p-3">
            <h3 className="text-sm font-medium">Outgoing graph edges</h3>
            <ul className="mt-2 list-disc pl-5 text-sm text-muted-foreground">
              {targets.map((target) =>
                target ? (
                  <li key={target}>
                    <code className="font-mono text-xs">{target}</code>
                  </li>
                ) : null,
              )}
            </ul>
          </section>
        </TabsContent>
        <TabsContent value="llm-calls" className="pt-4">
          <DistillNodeUsage
            bookId={bookId}
            runId={runId}
            runStatus={runStatus}
            nodeIds={DISTILL_DISPATCH_USAGE_COUNTERS}
            description="Calls and token counts from the parallel branches scheduled by this node."
            emptyMessage="Usage appears here when Distill has a run."
            noCallsMessage="No LLM calls have been recorded for the dispatched branches."
          />
        </TabsContent>
      </Tabs>
    </SideSheet>
  );
}

function DistillReportPipelineSheet({
  bookId,
  runId,
  runStatus,
  progress,
  status,
  kpi,
  onClose,
  onOpenNode,
  onPlayNode,
  replayDisabled,
  replayPending,
  replayPendingNodeId,
}: {
  bookId: string;
  runId: string | null;
  runStatus: DistillRun["status"] | null;
  progress: DistillRun["progress"] | null;
  status: AgenticNodeStatus;
  kpi: string | null;
  onClose: () => void;
  onOpenNode: (nodeId: string) => void;
  onPlayNode?: (nodeId: string) => void;
  replayDisabled: boolean;
  replayPending: boolean;
  replayPendingNodeId: string | null;
}) {
  const reportNodeIds = new Set<string>(DISTILL_REPORT_PIPELINE_NODE_IDS);
  const views = distillNodeViews(runStatus, progress)
    .filter((view) => reportNodeIds.has(view.id))
    .map((view) => ({ ...view, replayable: true }));

  return (
    <SideSheet
      title="Report pipeline"
      subtitle={
        <span className="flex flex-wrap items-center gap-x-3 gap-y-1">
          <AgenticStatusBadge status={status} />
          <span className="font-mono">distill · report_pipeline</span>
          {kpi ? <span className="font-medium tabular-nums">{kpi}</span> : null}
        </span>
      }
      closeLabel="Close report pipeline details"
      onClose={onClose}
    >
      <Tabs defaultValue="overview" className="w-full">
        <TabsList aria-label="Report pipeline details">
          <TabsTrigger value="overview">Overview</TabsTrigger>
          <TabsTrigger value="llm-calls">LLM Calls</TabsTrigger>
        </TabsList>
        <TabsContent value="overview" className="flex flex-col gap-4 pt-4">
          <div className="flex items-start justify-between gap-3">
            <p className="text-sm text-muted-foreground">
              {runStatus === "done" && status === "pending"
                ? "This subgraph was not part of the completed run path."
                : "This view expands the compiled report_pipeline subgraph. Its normal path runs in sequence. A targeted replay may start at a later report stage or skip this subgraph for visual-only work."}
            </p>
            {onPlayNode ? (
              <AgenticReplayButton
                label="Report pipeline"
                onPlay={() => onPlayNode("synthesize_book")}
                pending={replayPending && replayPendingNodeId === "synthesize_book"}
                disabled={replayDisabled || replayPending}
              />
            ) : null}
          </div>
          <div className="overflow-hidden rounded-sm border border-border bg-background">
            <AgenticFlow
              views={views}
              onOpenNode={onOpenNode}
              onPlayNode={onPlayNode}
              replayDisabled={replayDisabled}
              replayPending={replayPending}
              replayPendingNodeId={replayPendingNodeId}
              ariaLabel="Distill report pipeline LangGraph"
              connections={DISTILL_REPORT_PIPELINE_CONNECTIONS}
              positions={DISTILL_REPORT_PIPELINE_POSITIONS}
              className="h-[min(58vh,680px)] min-h-[360px]"
              fitView
            />
          </div>
        </TabsContent>
        <TabsContent value="llm-calls" className="pt-4">
          <DistillNodeUsage
            bookId={bookId}
            runId={runId}
            runStatus={runStatus}
            nodeIds={DISTILL_REPORT_PIPELINE_USAGE_COUNTERS}
            description="Calls and token counts across the stages inside this subgraph."
            emptyMessage="Usage appears here when Distill has a run."
            noCallsMessage="No LLM calls have been recorded for this report pipeline."
          />
        </TabsContent>
      </Tabs>
    </SideSheet>
  );
}

function DistillNodeUsage({
  bookId,
  runId,
  runStatus,
  nodeIds,
  description,
  emptyMessage,
  noCallsMessage,
}: {
  bookId: string;
  runId: string | null;
  runStatus: DistillRun["status"] | null;
  nodeIds: readonly string[];
  description: string;
  emptyMessage: string;
  noCallsMessage: string;
}) {
  const usageQuery = useQuery({
    queryKey: ["bd", "pipeline", "usage", bookId, runId] as const,
    queryFn: () => fetchPipelineUsage(bookId, runId ?? ""),
    enabled: runId !== null,
    refetchInterval: runStatus === "queued" || runStatus === "running" ? 3_000 : false,
    retry: false,
  });

  return (
    <section aria-label="Node LLM usage" className="flex flex-col gap-3">
      <div>
        <h3 className="text-sm font-medium">LLM usage</h3>
        <p className="mt-1 text-xs text-muted-foreground">{description}</p>
      </div>
      <PipelineUsageBreakdown
        usage={usageQuery.data}
        nodeIds={nodeIds}
        hasRun={runId !== null}
        loading={usageQuery.isPending}
        failed={usageQuery.isError}
        errorMessage={usageQuery.isError ? apiErrorMessage(usageQuery.error) : null}
        onRetry={() => void usageQuery.refetch()}
        emptyMessage={emptyMessage}
        noCallsMessage={noCallsMessage}
      />
    </section>
  );
}
