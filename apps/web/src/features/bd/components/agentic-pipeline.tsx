"use client";

import { ChartNoAxesColumn, Check, Info, X } from "lucide-react";
import dynamic from "next/dynamic";
import { useState } from "react";
import type { StreamStatus } from "@/shared/sse";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/shared/ui/tabs";
import {
  AGENTIC_NODE_DETAILS,
  AGENTIC_NODES,
  type AgenticNodeStatus,
  type AgenticRunSummary,
  agenticBoardCaption,
  agenticNodeCaption,
  agenticNodeStatus,
  agenticNodeViews,
  agenticPercent,
  chapterProgress,
  chapterProgressSummary,
  EMPTY_AGENTIC_PROGRESS,
} from "../domain/agentic-progress";
import { type AgenticUsageMode, agenticUsageRows, nodeUsageForAgenticStep } from "../domain/agentic-usage";
import type { BookUnits } from "../domain/books";
import type { AgenticProgress, AgenticRunStatus, AgenticUsage, ChapterRange } from "../entities";
import { useAgenticUsage } from "../hooks/use-agentic-run";
import { useModalDialog } from "../hooks/use-modal-dialog";
import { SideSheet } from "../rag/components/side-sheet";
import { ActionButton } from "./action-button";
import { AgenticNodeDetailsList, AgenticNodeList, AgenticPills, AgenticStatusBadge } from "./agentic-parts";
import { ToggleGroup } from "./toggle-group";

// The graph library is only needed once a graph is on screen.
const AgenticFlow = dynamic(() => import("./agentic-flow").then((module) => module.AgenticFlow), {
  ssr: false,
  loading: () => <p className="text-xs text-muted-foreground">Loading graph…</p>,
});

const MODES = [
  { id: "graph", label: "Graph" },
  { id: "list", label: "List" },
] as const;

const USAGE_MODES: readonly { id: AgenticUsageMode; label: string }[] = [
  { id: "agent", label: "By agent" },
  { id: "model", label: "By model" },
  { id: "agent_model", label: "By agent + model" },
];

export function AgenticProgressBar({
  status,
  progress,
  total,
  error,
  units,
  extractionDone,
  successful,
  streamStatus,
  actionPending,
  cancelPending,
  wipePending,
  hasRun,
  cancelRequested,
  onStart,
  onResume,
  onStop,
  onWipe,
}: {
  status: AgenticRunStatus["status"] | null;
  progress: AgenticProgress | null;
  total: number;
  error: string | null;
  units: BookUnits;
  extractionDone: boolean;
  successful: boolean;
  streamStatus: StreamStatus;
  actionPending: boolean;
  cancelPending: boolean;
  wipePending: boolean;
  hasRun: boolean;
  cancelRequested: boolean;
  onStart: () => void;
  onResume: () => void;
  onStop: () => void;
  onWipe: () => void;
}) {
  const completed = progress?.processed_pages.length ?? 0;
  const percent = agenticPercent(completed, total);
  const statusText = cancelRequested
    ? "Stopping"
    : status === null
      ? extractionDone
        ? "Ready to run"
        : "Not started"
      : status === "queued"
        ? "Queued"
        : status === "running"
          ? "Processing pages"
          : status;
  const resumable = status === "failed" || status === "cancelled";
  const startDisabled = actionPending || !extractionDone || status === "queued" || status === "running";

  return (
    <div className="min-w-0">
      <div className="flex min-h-9 flex-wrap items-center gap-2">
        <h3 className="flex items-center gap-1.5 text-sm font-medium">
          Ingestion Agentic AI
          {successful ? (
            <span
              role="img"
              aria-label="Completed successfully"
              title="Completed successfully"
              className="inline-flex text-success-text"
            >
              <Check className="size-4" aria-hidden="true" />
            </span>
          ) : null}
        </h3>
        <span aria-live="polite" className="text-xs text-muted-foreground">
          {statusText}
        </span>
        <div className="ml-auto flex flex-wrap items-center justify-end gap-2">
          <ActionButton
            onClick={resumable ? onResume : onStart}
            disabled={startDisabled}
            aria-busy={actionPending}
            aria-label={resumable ? "Resume Agentic AI" : "Start Agentic AI"}
            title={
              !extractionDone
                ? "Complete Docling extraction before starting Agentic AI."
                : status === "queued" || status === "running"
                  ? "An Agentic AI run is already active."
                  : status === "done"
                    ? "Start a new candidate; the completed run and its artifacts are preserved."
                    : undefined
            }
          >
            {actionPending ? (resumable ? "Resuming…" : "Starting…") : resumable ? "Resume" : "Start"}
          </ActionButton>
          <ActionButton
            onClick={onStop}
            disabled={!(status === "queued" || status === "running") || cancelPending || cancelRequested}
            aria-busy={cancelPending || cancelRequested}
            aria-label="Stop Agentic AI"
            title={
              cancelRequested
                ? "Stop requested; the active page operation is draining."
                : status === "queued" || status === "running"
                  ? undefined
                  : "There is no active Agentic AI run to stop."
            }
          >
            {cancelPending || cancelRequested ? "Stopping…" : "Stop"}
          </ActionButton>
          <ActionButton
            onClick={onWipe}
            disabled={!hasRun || status === "queued" || status === "running" || wipePending}
            aria-busy={wipePending}
            aria-label="Wipe Agentic AI"
            title={
              !hasRun
                ? "There are no Agentic AI artifacts to wipe."
                : status === "queued" || status === "running"
                  ? "Stop Agentic AI and wait for it to finish before wiping."
                  : undefined
            }
          >
            {wipePending ? "Wiping…" : "Wipe"}
          </ActionButton>
        </div>
      </div>
      <div
        role="progressbar"
        aria-label={`AI-processed ${units.plural}`}
        aria-valuemin={0}
        aria-valuemax={total > 0 ? total : 100}
        aria-valuenow={total > 0 ? completed : 0}
        aria-valuetext={`${completed} of ${total} ${units.plural} AI processed`}
        className="mt-2 h-1.5 w-full overflow-hidden rounded-sm bg-muted"
      >
        <div className="h-full bg-primary" style={{ width: `${percent ?? 0}%` }} />
      </div>
      {progress && progress.audit_warning_pages.length > 0 ? (
        <p className="mt-1 text-xs text-warning-text">
          Audit warnings on {progress.audit_warning_pages.length}{" "}
          {progress.audit_warning_pages.length === 1 ? units.singular : units.plural}.
        </p>
      ) : null}
      {error ? (
        <p role="alert" className="mt-1 break-words text-xs text-destructive-text">
          {error}
        </p>
      ) : null}
      {streamStatus === "reconnecting" ? (
        <p aria-live="polite" className="mt-1 text-xs text-warning-text">
          Connection lost — reconnecting…
        </p>
      ) : null}
      {cancelRequested ? (
        <p aria-live="polite" className="mt-1 text-xs text-muted-foreground">
          Stop requested; active page work is draining before the run ends.
        </p>
      ) : null}
    </div>
  );
}

export function AgenticPipelineOverview({
  bookId,
  run,
  status,
  progress,
  error,
  replayError = null,
  units,
  chapters,
  onOpenUsage,
  onPlayNode,
  replayPending = false,
  replayPendingNodeId = null,
}: {
  bookId: string;
  run: AgenticRunStatus | null;
  status: AgenticRunStatus["status"] | null;
  progress: AgenticProgress | null;
  error: string | null;
  replayError?: string | null;
  units: BookUnits;
  chapters: readonly ChapterRange[];
  onOpenUsage: () => void;
  onPlayNode?: (nodeId: string) => void;
  replayPending?: boolean;
  replayPendingNodeId?: string | null;
}) {
  const live = status === "queued" || status === "running";
  const usage = useAgenticUsage(bookId, run?.agentic_run_id ?? null, live, run !== null);
  const chapterRows = chapterProgress(chapters, progress?.processed_pages ?? []);
  const chapterSummary = chapterProgressSummary(chapters, progress?.processed_pages ?? []);
  const [mode, setMode] = useState<(typeof MODES)[number]["id"]>("graph");
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);
  const [chapterDetailsOpen, setChapterDetailsOpen] = useState(false);
  const selectedNode = AGENTIC_NODES.find((node) => node.id === selectedNodeId);
  const runStatus = status ?? run?.status ?? "pending";
  const total = run?.expected_page_count ?? 0;
  const persistedSelectionCount = (run?.selected_page_count ?? 0) + (run?.excluded_page_count ?? 0);
  const liveSelectionCount = (progress?.selected_page_count ?? 0) + (progress?.excluded_page_count ?? 0);
  const graphProgress = progress
    ? {
        ...progress,
        selected_page_count:
          liveSelectionCount === total
            ? progress.selected_page_count
            : persistedSelectionCount === total
              ? run?.selected_page_count
              : progress.selected_page_count,
        excluded_page_count:
          liveSelectionCount === total
            ? progress.excluded_page_count
            : persistedSelectionCount === total
              ? run?.excluded_page_count
              : progress.excluded_page_count,
      }
    : EMPTY_AGENTIC_PROGRESS;
  const summary: AgenticRunSummary | null =
    run?.status === "done"
      ? {
          deliveredPages: run.delivered_page_count,
          reviewPages: run.review_page_count,
          unverifiedPages: run.unverified_page_count,
        }
      : null;
  const views = agenticNodeViews(graphProgress, runStatus, total, units.plural, summary);
  const caption = agenticBoardCaption(
    graphProgress,
    runStatus,
    run !== null,
    graphProgress.processed_pages.length,
    total,
    units.plural,
  );

  return (
    <div className="min-w-0">
      <section
        aria-label="Agentic pipeline stages"
        className="min-w-0 rounded-md border border-border bg-card p-3"
      >
        <header className="flex min-w-0 items-start justify-between gap-3">
          <div className="min-w-0">
            <h3 className="text-sm font-medium">Agentic pipeline</h3>
            <p aria-live="polite" className="mt-1 text-xs text-muted-foreground">
              {caption}
            </p>
          </div>
          <ActionButton
            iconOnly
            onClick={() => setChapterDetailsOpen(true)}
            aria-label="Open chapter progress"
            title="Chapter progress"
            aria-haspopup="dialog"
          >
            <Info aria-hidden />
          </ActionButton>
        </header>
        {error ? (
          <p role="alert" className="mt-3 break-words text-sm text-destructive-text">
            Agentic processing failed — {error}
          </p>
        ) : null}
        {replayError ? (
          <p role="alert" className="mt-3 break-words text-sm text-destructive-text">
            {replayError}
          </p>
        ) : null}
        <div className="mt-3 grid min-w-0 grid-cols-1 gap-2 sm:grid-cols-[minmax(8rem,1fr)_minmax(0,2fr)]">
          <ActionButton
            className="w-full justify-center whitespace-nowrap px-2"
            onClick={onOpenUsage}
            aria-haspopup="dialog"
          >
            <ChartNoAxesColumn aria-hidden />
            LLM usage
          </ActionButton>
          <ToggleGroup
            legend="Pipeline shown as"
            options={MODES}
            value={mode}
            onChange={setMode}
            fullWidth
          />
        </div>
        <div className="mt-3 min-w-0 overflow-hidden rounded-sm border border-border bg-background">
          {mode === "graph" ? (
            <AgenticFlow
              views={views}
              onOpenNode={setSelectedNodeId}
              onPlayNode={onPlayNode}
              replayPending={replayPending}
              replayPendingNodeId={replayPendingNodeId}
            />
          ) : mode === "list" ? (
            <div className="p-3">
              <AgenticNodeList
                rows={views}
                onOpen={setSelectedNodeId}
                onPlay={onPlayNode}
                replayPending={replayPending}
                replayPendingNodeId={replayPendingNodeId}
              />
            </div>
          ) : null}
        </div>
        {progress && progress.audit_warning_pages.length > 0 ? (
          <p className="mt-3 text-xs text-warning-text">
            Audit warnings · {progress.audit_warning_pages.length}{" "}
            {progress.audit_warning_pages.length === 1 ? "page" : "pages"} · Pages{" "}
            {progress.audit_warning_pages.join(", ")}
          </p>
        ) : null}
      </section>
      {chapterDetailsOpen ? (
        <SideSheet
          title="Chapter progress"
          subtitle={
            chapterSummary
              ? `${chapterSummary.completed} of ${chapterSummary.total} chapters complete`
              : "Progress by chapter"
          }
          closeLabel="Close chapter progress"
          onClose={() => setChapterDetailsOpen(false)}
        >
          <section aria-label="Chapter progress entries" className="rounded-sm border border-border bg-card p-4">
            {chapterRows.length > 0 ? (
              <ol
                // biome-ignore lint/a11y/noNoninteractiveTabindex: a long chapter list scrolls, so keyboard users must be able to focus it to scroll it (WCAG 2.1.1)
                tabIndex={0}
                className="flex max-h-[calc(100dvh-12rem)] min-h-0 flex-col gap-4 overflow-y-auto"
              >
                {chapterRows.map((chapter) => {
                  const percent = agenticPercent(chapter.processed, chapter.total);
                  return (
                    <li key={`${chapter.first_page}:${chapter.last_page}`} className="min-w-0 list-none">
                      <div className="flex flex-wrap items-center justify-between gap-2 text-xs">
                        <span className="min-w-0 break-words">{chapter.title}</span>
                        <span className="shrink-0 tabular-nums text-muted-foreground">
                          {chapter.processed}/{chapter.total} {units.plural}
                        </span>
                      </div>
                      <div
                        role="progressbar"
                        aria-label={`${chapter.title} progress`}
                        aria-valuemin={0}
                        aria-valuemax={chapter.total}
                        aria-valuenow={chapter.processed}
                        className="mt-1 h-1 w-full overflow-hidden rounded-sm bg-muted"
                      >
                        <div className="h-full bg-primary" style={{ width: `${percent ?? 0}%` }} />
                      </div>
                    </li>
                  );
                })}
              </ol>
            ) : (
              <p className="text-sm text-muted-foreground">Chapter ranges are not available for this book.</p>
            )}
          </section>
        </SideSheet>
      ) : null}
      {selectedNode ? (
        <AgenticNodeSheet
          key={selectedNode.id}
          node={selectedNode}
          status={agenticNodeStatus(graphProgress, selectedNode.id, runStatus)}
          caption={agenticNodeCaption(
            graphProgress,
            selectedNode.id,
            total,
            units.plural,
            agenticNodeStatus(graphProgress, selectedNode.id, runStatus),
          )}
          kpi={views.find((view) => view.id === selectedNode.id)?.kpi ?? null}
          usage={usage.data}
          loading={run !== null && usage.isPending}
          failed={usage.isError}
          hasRun={run !== null}
          onClose={() => setSelectedNodeId(null)}
        />
      ) : null}
    </div>
  );
}

export function AgenticPipelineUsage({
  bookId,
  run,
  status,
}: {
  bookId: string;
  run: AgenticRunStatus | null;
  status: AgenticRunStatus["status"] | null;
}) {
  const live = status === "queued" || status === "running";
  const usage = useAgenticUsage(bookId, run?.agentic_run_id ?? null, live, run !== null);

  return (
    <PipelineUsageTotal
      usage={usage.data}
      hasRun={run !== null}
      loading={run !== null && usage.isPending}
      failed={run !== null && usage.isError}
    />
  );
}

const PENDING_NOTE: Record<AgenticNodeStatus, string | null> = {
  done: null,
  failed: "This node failed before it finished. The run's error is shown above the graph.",
  running: "Running — its results appear here the moment the node commits.",
  pending: "This node has not run yet.",
  cancelled: "The run stopped before this node finished.",
};

/** A side sheet for one graph node, as the Docs Distiller's: what it does and what it reads and writes (Overview), then
 * the LLM calls it made. It stays live while the run goes on. */
function AgenticNodeSheet({
  node,
  status,
  caption,
  kpi,
  usage,
  loading,
  failed,
  hasRun,
  onClose,
}: {
  node: (typeof AGENTIC_NODES)[number];
  status: AgenticNodeStatus;
  caption: string | null;
  kpi: string | null;
  usage: AgenticUsage | undefined;
  loading: boolean;
  failed: boolean;
  hasRun: boolean;
  onClose: () => void;
}) {
  const dialog = useModalDialog();
  const details = AGENTIC_NODE_DETAILS[node.id];
  const note = PENDING_NOTE[status];

  return (
    <dialog
      ref={dialog}
      aria-labelledby="bd-agentic-node-title"
      onCancel={(event) => {
        event.preventDefault();
        onClose();
      }}
      className="m-0 ml-auto h-dvh max-h-none w-full max-w-2xl overflow-y-auto border-l border-border bg-background p-0 text-foreground backdrop:bg-background/80"
    >
      <header className="sticky top-0 z-10 flex items-start gap-3 border-b border-border bg-background px-5 py-4">
        <div className="min-w-0 flex-1">
          <h2 id="bd-agentic-node-title" className="text-lg font-semibold tracking-tight">
            {details.title}
          </h2>
          <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1">
            <AgenticStatusBadge status={status} />
            <span className="font-mono text-xs text-muted-foreground">ingestion · {node.id}</span>
            <span className="font-mono text-xs text-muted-foreground">{details.kind}</span>
            {kpi ? (
              <span className="text-xs font-medium tabular-nums">{kpi}</span>
            ) : caption ? (
              <span className="text-xs text-muted-foreground">{caption}</span>
            ) : null}
          </div>
        </div>
        <ActionButton iconOnly onClick={onClose} aria-label="Close node details">
          <X aria-hidden />
        </ActionButton>
      </header>
      <div className="flex flex-col gap-5 px-5 py-4">
        <Tabs defaultValue="overview" className="w-full">
          <TabsList aria-label="Node details">
            <TabsTrigger value="overview">Overview</TabsTrigger>
            <TabsTrigger value="llm-calls">LLM Calls</TabsTrigger>
            <TabsTrigger value="resources">Resources</TabsTrigger>
          </TabsList>
          <TabsContent value="overview" className="flex flex-col gap-3 pt-4">
            <p className="text-sm">{details.subtitle}</p>
            <ul className="list-disc space-y-1 pl-5 text-sm text-muted-foreground">
              {details.actions.map((action) => (
                <li key={action}>{action}</li>
              ))}
            </ul>
            <div className="grid grid-cols-[auto_1fr] items-baseline gap-x-3 gap-y-2 text-xs text-muted-foreground">
              <span>Reads</span>
              <AgenticPills items={details.reads} />
              <span>Writes</span>
              <AgenticPills items={details.writes} />
              <span>LLM</span>
              <span className="text-sm">{details.llm}</span>
            </div>
            {note ? <p role="status" className="text-sm text-muted-foreground">{note}</p> : null}
          </TabsContent>
          <TabsContent value="llm-calls" className="flex flex-col gap-3 pt-4">
            <UsageBreakdown usage={usage} nodeId={node.id} loading={loading} failed={failed} hasRun={hasRun} />
          </TabsContent>
          <TabsContent value="resources" className="pt-4">
            <section aria-label="Agents, skills, and tools">
              <AgenticNodeDetailsList resources={details.resources} />
            </section>
          </TabsContent>
        </Tabs>
      </div>
    </dialog>
  );
}

function PipelineUsageTotal({
  usage,
  hasRun,
  loading,
  failed,
}: {
  usage: ReturnType<typeof useAgenticUsage>["data"];
  hasRun: boolean;
  loading: boolean;
  failed: boolean;
}) {
  return (
    <section aria-label="Pipeline usage" className="min-w-0 rounded-md border border-border bg-card p-3">
      <h3 className="text-sm font-medium">Pipeline usage</h3>
      <p className="mt-1 text-xs text-muted-foreground">Agentic ingestion calls and token counts.</p>
      {usage ? (
        <dl className="mt-3 grid grid-cols-1 gap-2">
          {[
            ["LLM calls", usage.total.calls],
            ["Input tokens", usage.total.tokens_in],
            ["Output tokens", usage.total.tokens_out],
            ["Reasoning tokens", usage.total.reasoning_tokens],
            ["Errors", usage.total.errors],
          ].map(([label, value]) => (
            <div key={label} className="rounded-sm border border-border px-3 py-2">
              <dt className="text-xs text-muted-foreground">{label}</dt>
              <dd className="text-lg font-semibold tabular-nums">{Number(value).toLocaleString()}</dd>
            </div>
          ))}
        </dl>
      ) : failed ? (
        <p role="status" className="mt-3 text-sm text-warning-text">
          Usage is unavailable right now. Open LLM usage to retry.
        </p>
      ) : (
        <p role="status" className="mt-3 text-sm text-muted-foreground">
          {loading ? "Loading usage…" : hasRun ? "No usage has been reported yet." : "No Agentic AI run yet."}
        </p>
      )}
    </section>
  );
}

export function AgenticUsageSheet({
  open,
  bookId,
  run,
  usageRunId,
  live,
  source = "ingestion",
  onClose,
}: {
  open: boolean;
  bookId: string;
  /** Null before the first run, and again after its data was wiped: the sheet still opens and says so. */
  run: AgenticRunStatus | null;
  usageRunId?: string | null;
  live: boolean;
  source?: "ingestion" | "retrieval-index";
  onClose: () => void;
}) {
  return open ? (
    <UsageSheet
      bookId={bookId}
      runId={usageRunId === undefined ? (run?.agentic_run_id ?? null) : usageRunId}
      live={live}
      source={source}
      onClose={onClose}
    />
  ) : null;
}

function UsageSheet({
  bookId,
  runId,
  live,
  source,
  onClose,
}: {
  bookId: string;
  runId: string | null;
  live: boolean;
  source: "ingestion" | "retrieval-index";
  onClose: () => void;
}) {
  const dialog = useModalDialog();
  const usage = useAgenticUsage(bookId, runId, live, runId !== null, source);
  const title = source === "ingestion" ? "Ingestion LLM usage" : "Agentic RAG LLM usage";
  const titleId = source === "ingestion" ? "bd-ingestion-usage-title" : "bd-retrieval-index-usage-title";

  return (
    <dialog
      ref={dialog}
      aria-labelledby={titleId}
      onCancel={(event) => {
        event.preventDefault();
        onClose();
      }}
      className="m-0 ml-auto h-dvh max-h-none w-full max-w-2xl overflow-y-auto border-l border-border bg-background p-0 text-foreground backdrop:bg-background/80"
    >
      <header className="sticky top-0 z-10 flex items-start gap-3 border-b border-border bg-background px-5 py-4">
        <div className="min-w-0 flex-1">
          <h2 id={titleId} className="text-lg font-semibold tracking-tight">
            {title}
          </h2>
          <p className="mt-1 text-sm text-muted-foreground">
            Calls and token counts, grouped by processing node.
          </p>
        </div>
        <ActionButton iconOnly onClick={onClose} aria-label="Close usage">
          <X aria-hidden />
        </ActionButton>
      </header>
      <div className="flex flex-col gap-4 px-5 py-4">
        <UsageBreakdown
          usage={usage.data}
          loading={runId !== null && usage.isPending}
          failed={usage.isError}
          hasRun={runId !== null}
        />
      </div>
    </dialog>
  );
}

function UsageBreakdown({
  usage,
  nodeId,
  loading,
  failed,
  hasRun,
}: {
  usage: AgenticUsage | undefined;
  nodeId?: (typeof AGENTIC_NODES)[number]["id"];
  loading: boolean;
  failed: boolean;
  hasRun: boolean;
}) {
  const [mode, setMode] = useState<AgenticUsageMode>("agent_model");
  const counts = nodeId ? nodeUsageForAgenticStep(usage, nodeId) : usage?.total;
  const rows = agenticUsageRows(usage, mode, nodeId);
  const caption =
    mode === "agent" ? "Usage by agent" : mode === "model" ? "Usage by model" : "Usage by agent and model";
  const metrics = [
    ["LLM calls", counts?.calls ?? 0],
    ["Input tokens", counts?.tokens_in ?? 0],
    ["Output tokens", counts?.tokens_out ?? 0],
    ["Reasoning tokens", counts?.reasoning_tokens ?? 0],
    ["Errors", counts?.errors ?? 0],
  ] as const;

  return (
    <>
      {usage ? (
        <dl className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-5">
          {metrics.map(([label, value]) => (
            <div key={label} className="rounded-sm border border-border px-3 py-2">
              <dt className="text-xs text-muted-foreground">{label}</dt>
              <dd className="text-lg font-semibold tabular-nums">{value.toLocaleString()}</dd>
            </div>
          ))}
        </dl>
      ) : null}
      {hasRun ? (
        <ToggleGroup legend="Group usage by" options={USAGE_MODES} value={mode} onChange={setMode} />
      ) : null}
      {failed ? (
        <p role="alert" className="text-sm text-destructive-text">
          LLM usage could not be loaded.
        </p>
      ) : loading && !usage ? (
        <p role="status" className="text-sm text-muted-foreground">
          Loading LLM usage…
        </p>
      ) : !hasRun ? (
        <p className="text-sm text-muted-foreground">
          {nodeId
            ? "Usage will appear after this node runs."
            : "No Agentic AI run yet. Usage appears here once one has started."}
        </p>
      ) : rows.length > 0 ? (
        <AgenticUsageTable rows={rows} mode={mode} caption={caption} />
      ) : (
        <p className="text-sm text-muted-foreground">
          {nodeId
            ? "No LLM calls have been recorded for this node. This may be expected for deterministic stages."
            : "No LLM calls have been recorded for this run."}
        </p>
      )}
    </>
  );
}

function AgenticUsageTable({
  rows,
  mode,
  caption,
}: {
  rows: ReturnType<typeof agenticUsageRows>;
  mode: AgenticUsageMode;
  caption: string;
}) {
  const showAgent = mode !== "model";
  const showModel = mode !== "agent";

  return (
    <section
      aria-label={caption}
      // biome-ignore lint/a11y/noNoninteractiveTabindex: a wide table scrolls, so keyboard users must be able to focus it to scroll it (WCAG 2.1.1)
      tabIndex={0}
      className="overflow-x-auto rounded-sm border border-border"
    >
      <table className="w-full min-w-[42rem] border-collapse text-sm">
        <caption className="border-b border-border px-3 py-2 text-left text-xs font-medium text-muted-foreground">
          {caption}
        </caption>
        <thead>
          <tr className="border-b border-border text-left text-xs text-muted-foreground">
            {showAgent ? (
              <th scope="col" className="px-3 py-2 font-medium">
                Agent
              </th>
            ) : null}
            {showModel ? (
              <>
                <th scope="col" className="px-3 py-2 font-medium">
                  Provider
                </th>
                <th scope="col" className="px-3 py-2 font-medium">
                  Model
                </th>
              </>
            ) : null}
            <th scope="col" className="px-3 py-2 text-right font-medium">
              Calls
            </th>
            <th scope="col" className="px-3 py-2 text-right font-medium">
              Input tokens
            </th>
            <th scope="col" className="px-3 py-2 text-right font-medium">
              Output tokens
            </th>
            <th scope="col" className="px-3 py-2 text-right font-medium">
              Reasoning
            </th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.key} className="border-b border-border last:border-b-0">
              {showAgent ? (
                <th scope="row" className="px-3 py-1.5 text-left font-normal">
                  {row.agent}
                </th>
              ) : null}
              {showModel ? (
                <>
                  <td className="break-all px-3 py-1.5 font-mono text-xs">{row.provider}</td>
                  <td className="break-all px-3 py-1.5 font-mono text-xs">{row.model}</td>
                </>
              ) : null}
              <td className="px-3 py-1.5 text-right tabular-nums">{row.calls.toLocaleString()}</td>
              <td className="px-3 py-1.5 text-right tabular-nums">{row.tokens_in.toLocaleString()}</td>
              <td className="px-3 py-1.5 text-right tabular-nums">{row.tokens_out.toLocaleString()}</td>
              <td className="px-3 py-1.5 text-right tabular-nums">{row.reasoning_tokens.toLocaleString()}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}
