"use client";

import { ChartNoAxesColumn } from "lucide-react";
import dynamic from "next/dynamic";
import { useState } from "react";
import { compiledTopology } from "../domain/compiled-topology";
import { isRunTerminal, tierLabel } from "../domain/ingestion";
import {
  INGESTION_NODE_DETAILS,
  INGESTION_NODES,
  ingestionNodeCaption,
  ingestionNodeKpis,
  ingestionNodeStatus,
  ingestionNodeViews,
  ingestionPercent,
  pageProgress,
  pageProgressSummary,
  phaseStatusText,
} from "../domain/ingestion-progress";
import type { RunStatus } from "../entities";
import { useIngestionUsage } from "../hooks/use-dd-data";
import { useIngestionGraph } from "../hooks/use-stage-queries";
import { ActionButton } from "./action-button";
import { AgenticProgress, ExtractionProgress } from "./ingestion-progress";
import { IngestionNodeSheet, IngestionUsageSheet, PipelineUsageTotal } from "./ingestion-usage";
import { LangGraphTopology } from "./langgraph-topology";
import type { StageFlowNodeDetails } from "./stage-flow";
import { StageNodes } from "./stage-parts";
import { ToggleGroup } from "./toggle-group";

// The graph library is only needed once a graph is on screen.
const StageFlow = dynamic(() => import("./stage-flow").then((module) => module.StageFlow), {
  ssr: false,
  loading: () => <p className="text-xs text-muted-foreground">Loading graph…</p>,
});

// Ingestion has no per-node live line or graph state; the shared graph still asks for them.
const NO_LIVE_LINES: Record<string, string> = {};
const NO_VALUES: Record<string, unknown> = {};

function fallbackNodeTitle(id: string): string {
  return id
    .split("_")
    .map((part) => `${part.charAt(0).toUpperCase()}${part.slice(1)}`)
    .join(" ");
}

const MODES = [
  { id: "graph", label: "Graph" },
  { id: "list", label: "List" },
  { id: "langgraph", label: "LangGraph" },
] as const;

type Progress = NonNullable<RunStatus["progress"]>;

export function IngestionPipeline({
  runId,
  progress,
  ingested,
  ingestedPages,
  startPending,
  wipePending,
  agenticActionPending,
  agenticCancelPending,
  agenticWipePending,
  cancelRequested,
  cancelError,
  statusUnavailable,
  onStart,
  onStop,
  onWipe,
  onStartAgentic,
  onResumeAgentic,
  onStopAgentic,
  onWipeAgentic,
}: {
  runId: string | null;
  progress: Progress | null;
  /** The framework is in the library. */
  ingested: boolean;
  ingestedPages: number;
  startPending: boolean;
  wipePending: boolean;
  agenticActionPending: boolean;
  agenticCancelPending: boolean;
  agenticWipePending: boolean;
  cancelRequested: boolean;
  cancelError: string | null;
  statusUnavailable: boolean;
  onStart: () => void;
  onStop: () => void;
  onWipe: () => void;
  onStartAgentic: () => void;
  onResumeAgentic: () => void;
  onStopAgentic: () => void;
  onWipeAgentic: () => void;
}) {
  const [mode, setMode] = useState<(typeof MODES)[number]["id"]>("graph");
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);
  const [usageOpen, setUsageOpen] = useState(false);
  const graphQuery = useIngestionGraph();
  const graphTopology = graphQuery.source;
  const runStatus = progress?.status ?? null;
  const hasRun = runId !== null;
  const terminal = isRunTerminal(runStatus);
  // A run in the address that can no longer be read (its progress expired) is not in flight.
  const live = hasRun && !terminal && !statusUnavailable;
  const usage = useIngestionUsage(runId, live);
  const extraction = progress?.stages?.extraction ?? null;
  const agentic = progress?.stages?.agentic ?? null;
  const extractionActive = live && extraction?.status === "running";
  const agenticActive = live && (agentic?.status === "running" || agentic?.status === "queued");
  const extractionStopping = extractionActive && cancelRequested;
  const agenticStopping = agenticActive && cancelRequested;

  // Without a run on screen the library says the pages are there; with one, the run is the truth.
  const extractionDone = hasRun ? extraction?.status === "done" : ingested;
  const extractionTotal = hasRun ? (extraction?.total ?? 0) : ingestedPages;
  const extractionCurrent = hasRun ? (extraction?.current ?? 0) : ingestedPages;
  const extractionStatusText = hasRun
    ? extractionStopping
      ? "Stopping"
      : phaseStatusText(extraction?.status, runStatus)
    : ingested
      ? "Complete"
      : "Not started";
  const agenticStatusText = hasRun
    ? agenticStopping
      ? "Stopping"
      : phaseStatusText(agentic?.status, runStatus)
    : ingested
      ? "Ready to run"
      : "Waiting";
  const nodeViews = ingestionNodeViews(agentic, runStatus);
  const nodeKpis = ingestionNodeKpis(agentic);
  const topology = compiledTopology(graphTopology, "ingestion");
  const graphNodeViews =
    topology?.nodeIds.map((id) => ({
      node: { id, label: INGESTION_NODE_DETAILS[id]?.title ?? fallbackNodeTitle(id) },
      status: ingestionNodeStatus(agentic, id, runStatus),
    })) ?? [];
  const graphNodeDetails: Record<string, StageFlowNodeDetails> = {};
  const fallbackPageTotal = agentic?.total && agentic.total > 0 ? agentic.total : extractionTotal;
  for (const node of INGESTION_NODES) {
    const description = INGESTION_NODE_DETAILS[node.id];
    const reported = agentic?.node_progress[node.id];
    const total = reported?.total && reported.total > 0 ? reported.total : fallbackPageTotal;
    const current =
      ingestionNodeStatus(agentic, node.id, runStatus) === "done" && total > 0
        ? total
        : Math.min(Math.max(reported?.current ?? 0, 0), total);
    graphNodeDetails[node.id] = {
      agents: description?.agents ?? [],
      skills: description?.skills ?? [],
      tools: description?.tools ?? [],
      progress: description?.pageProgressLabel
        ? {
            current,
            total,
            percent: ingestionPercent(current, total) ?? 0,
            label: description.pageProgressLabel,
          }
        : null,
    };
  }
  const agenticCurrent = agentic?.current ?? 0;
  const agenticTotal = agentic?.total ?? 0;
  const auditTotal = agentic?.node_progress.audit_pages;
  const retryable = runStatus === "failed" || runStatus === "cancelled";
  const agenticRetryable = agentic?.status === "failed" || agentic?.status === "cancelled";
  const pageRows = pageProgress(agentic);
  const pageSummary = pageProgressSummary(pageRows);
  const selectedNode = INGESTION_NODES.find((node) => node.id === selectedNodeId);
  const usageFailed = hasRun && usage.isError;
  const usageLoading = hasRun && usage.isPending;
  const agenticError = agentic?.error ?? progress?.error ?? null;

  return (
    <div>
      <div className="mb-4 grid grid-cols-1 items-stretch gap-3 lg:grid-cols-2">
        <ExtractionProgress
          statusText={extractionStatusText}
          current={extractionCurrent}
          total={extractionTotal}
          tierText={extraction?.tier ? tierLabel(extraction.tier) : null}
          lastUrl={extraction?.last_url ?? null}
          successful={extractionDone && !extraction?.error}
          live={live}
          extracting={extractionActive}
          ingested={ingested}
          retryable={retryable}
          stopping={extractionStopping}
          startPending={startPending}
          wipePending={wipePending}
          statusUnavailable={statusUnavailable}
          cancelError={cancelError}
          finishedText={terminal ? `Ingestion ${phaseStatusText(runStatus, runStatus).toLowerCase()}.` : null}
          onStart={onStart}
          onStop={onStop}
          onWipe={onWipe}
        />
        <AgenticProgress
          statusText={agenticStatusText}
          current={agenticCurrent}
          total={agenticTotal}
          audited={auditTotal?.current ?? 0}
          successful={agentic?.status === "done" && !agentic.error}
          live={live}
          stopping={agenticStopping}
          preparing={progress?.tier === "post" && !terminal}
          error={agenticError}
          active={agenticActive}
          actionPending={agenticActionPending}
          cancelPending={agenticCancelPending}
          wipePending={agenticWipePending}
          hasRun={hasRun}
          ready={ingested}
          retryable={agenticRetryable}
          cancelError={cancelError}
          onStart={onStartAgentic}
          onResume={onResumeAgentic}
          onStop={onStopAgentic}
          onWipe={onWipeAgentic}
        />
      </div>

      <div className="grid grid-cols-1 gap-4 xl:grid-cols-[minmax(0,4fr)_minmax(0,1fr)]">
        <section
          aria-label="Agentic pipeline stages"
          className="min-w-0 rounded-md border border-border bg-card p-3"
        >
          <header className="flex flex-wrap items-start justify-between gap-3">
            <div>
              <h3 className="text-sm font-medium">Agentic pipeline</h3>
              <p className="mt-1 text-xs text-muted-foreground">
                {hasRun
                  ? `Run ${runStatus ?? "pending"} · ${agenticCurrent} of ${agenticTotal} pages normalized`
                  : "Agentic AI is ready to run after extraction."}
              </p>
            </div>
          </header>
          <div className="mt-3 grid min-w-0 grid-cols-1 gap-3 xl:grid-cols-[minmax(0,3fr)_minmax(16rem,1fr)]">
            <div className="min-w-0">
              {mode === "graph" ? (
                topology ? (
                  <StageFlow
                    stage="ingestion"
                    title="Agentic AI ingestion"
                    views={graphNodeViews}
                    kpis={nodeKpis}
                    liveLines={NO_LIVE_LINES}
                    values={NO_VALUES}
                    live={live}
                    edges={topology.edges}
                    nodeDetails={graphNodeDetails}
                    onOpen={setSelectedNodeId}
                  />
                ) : (
                  <p role="status" className="text-xs text-muted-foreground">
                    {graphQuery.loading
                      ? "Loading compiled graph…"
                      : "Compiled graph topology is unavailable."}
                  </p>
                )
              ) : mode === "list" ? (
                <StageNodes
                  label="Agentic pipeline nodes"
                  views={nodeViews}
                  kpis={nodeKpis}
                  liveLines={NO_LIVE_LINES}
                  nodeDetails={graphNodeDetails}
                  onOpen={setSelectedNodeId}
                />
              ) : (
                <LangGraphTopology
                  title="Ingestion Agentic AI"
                  source={graphTopology}
                  loading={graphQuery.loading}
                />
              )}
            </div>
            <div className="flex min-w-0 flex-col">
              <div className="mb-3 flex min-w-0 flex-col gap-2">
                {hasRun ? (
                  <ActionButton
                    className="w-full justify-center whitespace-nowrap px-2"
                    onClick={() => setUsageOpen(true)}
                    aria-haspopup="dialog"
                  >
                    <ChartNoAxesColumn aria-hidden />
                    LLM usage
                  </ActionButton>
                ) : null}
                <ToggleGroup
                  legend="Pipeline shown as"
                  options={MODES}
                  value={mode}
                  onChange={setMode}
                  fullWidth
                />
              </div>
              {/* From xl the box is as tall as the graph beside it and nothing more: its content is taken out of flow
               * (`absolute inset-0`), so a long list scrolls inside it instead of stretching the row. */}
              <section
                aria-label="Page progress"
                className="relative min-w-0 rounded-sm border border-border bg-background xl:min-h-72 xl:flex-1"
              >
                <div className="flex min-h-0 flex-col p-3 xl:absolute xl:inset-0">
                  <header className="flex flex-wrap items-baseline justify-between gap-2">
                    <h4 className="text-sm font-medium">Page progress</h4>
                    {pageSummary ? (
                      <span className="text-xs tabular-nums text-muted-foreground">
                        {pageSummary.completed} of {pageSummary.total} complete
                      </span>
                    ) : null}
                  </header>
                  {pageRows.length > 0 ? (
                    <ol
                      aria-label="Page progress entries"
                      // biome-ignore lint/a11y/noNoninteractiveTabindex: a long list scrolls, so keyboard users must be able to focus it to scroll it (WCAG 2.1.1)
                      tabIndex={0}
                      className="mt-3 flex max-h-[60dvh] min-h-0 flex-col gap-3 overflow-y-auto xl:max-h-none xl:flex-1"
                    >
                      {pageRows.map((row) => {
                        const percent = ingestionPercent(row.processed, row.total);
                        return (
                          <li key={row.id} className="min-w-0">
                            <div className="flex flex-wrap items-center justify-between gap-2 text-xs">
                              <span className="min-w-0 break-words">{row.label}</span>
                              <span className="shrink-0 tabular-nums text-muted-foreground">
                                {row.processed}/{row.total} pages
                              </span>
                            </div>
                            <div
                              role="progressbar"
                              aria-label={`${row.label} progress`}
                              aria-valuemin={0}
                              aria-valuemax={row.total}
                              aria-valuenow={row.processed}
                              className="mt-1 h-1 w-full overflow-hidden rounded-sm bg-muted"
                            >
                              <div className="h-full bg-primary" style={{ width: `${percent ?? 0}%` }} />
                            </div>
                          </li>
                        );
                      })}
                    </ol>
                  ) : (
                    <p className="mt-3 text-xs text-muted-foreground">
                      Page counts appear once Agentic AI starts.
                    </p>
                  )}
                </div>
              </section>
            </div>
          </div>
        </section>
        <PipelineUsageTotal usage={usage.data} hasRun={hasRun} loading={usageLoading} failed={usageFailed} />
        {selectedNode ? (
          <IngestionNodeSheet
            node={selectedNode}
            status={ingestionNodeStatus(agentic, selectedNode.id, runStatus)}
            caption={ingestionNodeCaption(agentic, selectedNode.id)}
            usage={usage.data}
            loading={usageLoading}
            failed={usageFailed}
            hasRun={hasRun}
            onClose={() => setSelectedNodeId(null)}
          />
        ) : null}
        {usageOpen && hasRun ? (
          <IngestionUsageSheet
            usage={usage.data}
            loading={usageLoading}
            failed={usageFailed}
            onClose={() => setUsageOpen(false)}
          />
        ) : null}
      </div>
    </div>
  );
}
