"use client";

import { ChartNoAxesColumn, Check, X } from "lucide-react";
import dynamic from "next/dynamic";
import { useState } from "react";
import { MermaidTopology } from "@/shared/markdown/mermaid-topology";
import type { StreamStatus } from "@/shared/sse";
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
import { useAgenticGraph } from "../hooks/use-bd-data";
import { useModalDialog } from "../hooks/use-modal-dialog";
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
  { id: "langgraph", label: "LangGraph" },
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
  qualityStatus,
  deliveredPageCount,
  discardedBlankPageCount,
  reviewPageCount,
  unverifiedPageCount,
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
  chapters,
}: {
  status: AgenticRunStatus["status"] | null;
  progress: AgenticProgress | null;
  total: number;
  error: string | null;
  units: BookUnits;
  extractionDone: boolean;
  successful: boolean;
  qualityStatus: AgenticRunStatus["quality_status"];
  deliveredPageCount: number;
  discardedBlankPageCount: number;
  reviewPageCount: number;
  unverifiedPageCount: number;
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
  chapters: readonly ChapterRange[];
}) {
  const completed = progress?.processed_pages.length ?? 0;
  const percent = agenticPercent(completed, total);
  const chapterSummary = chapterProgressSummary(chapters, progress?.processed_pages ?? []);
  const activeWork = [
    progress?.active_audit_pages.length ? `${progress.active_audit_pages.length} auditing` : null,
    progress?.active_repair_pages.length ? `${progress.active_repair_pages.length} repairing` : null,
  ].filter((item) => item !== null);
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
      <div className="flex min-h-9 flex-wrap items-center justify-between gap-2">
        <h3 className="flex items-center gap-1.5 text-sm font-medium">
          Agentic AI
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
        <div className="flex flex-wrap items-center justify-end gap-2">
          <span aria-live="polite" className="text-xs text-muted-foreground">
            {statusText}
          </span>
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
      <dl className="mt-2 grid grid-cols-2 gap-2 text-xs sm:grid-cols-3 xl:grid-cols-6">
        <ProgressMetric
          label="Processed"
          value={`${completed} / ${total > 0 ? total : "?"}${percent !== null ? ` · ${percent}%` : ""}`}
        />
        <ProgressMetric label="Audited" value={progress ? String(progress.audited_pages.length) : "—"} />
        <ProgressMetric
          label="Needs review"
          value={status === "done" ? String(reviewPageCount) : hasRun ? "Pending" : "—"}
        />
        <ProgressMetric
          label="Unverified audits"
          value={status === "done" ? String(unverifiedPageCount) : hasRun ? "Pending" : "—"}
        />
        <ProgressMetric
          label="Chapters"
          value={chapterSummary ? `${chapterSummary.completed} / ${chapterSummary.total}` : "—"}
        />
        <ProgressMetric label="Active work" value={activeWork.length ? activeWork.join(" · ") : "Idle"} />
      </dl>
      {progress && progress.audit_warning_pages.length > 0 ? (
        <p className="mt-1 text-xs text-warning-text">
          Audit warnings on {progress.audit_warning_pages.length}{" "}
          {progress.audit_warning_pages.length === 1 ? units.singular : units.plural}.
        </p>
      ) : null}
      {qualityStatus === "complete_with_review" ? (
        <p className="mt-1 text-xs text-warning-text">
          Run needs attention · {reviewPageCount}{" "}
          {reviewPageCount === 1 ? "page needs review" : "pages need review"} · {unverifiedPageCount}{" "}
          {unverifiedPageCount === 1 ? "audit unverified" : "audits unverified"} · {deliveredPageCount}{" "}
          delivered · {discardedBlankPageCount} blank excluded
        </p>
      ) : qualityStatus === "complete" ? (
        <p className="mt-1 text-xs text-success-text">
          Quality checks passed · {deliveredPageCount} delivered · {discardedBlankPageCount} blank excluded
        </p>
      ) : status === "done" ? (
        <p className="mt-1 text-xs text-warning-text">Quality status unavailable for this run.</p>
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
      ) : status === "queued" || status === "running" ? (
        <p className="mt-1 text-xs text-muted-foreground">
          Stop takes effect at a safe page or graph-stage boundary. Wipe becomes available after the run
          stops.
        </p>
      ) : null}
    </div>
  );
}

function ProgressMetric({ label, value }: { label: string; value: string }) {
  return (
    <div className="min-w-0 rounded-sm border border-border bg-background px-2.5 py-1.5">
      <dt className="text-muted-foreground">{label}</dt>
      <dd className="break-words font-medium tabular-nums text-foreground">{value}</dd>
    </div>
  );
}

export function AgenticPipelineOverview({
  bookId,
  run,
  status,
  progress,
  error,
  units,
  chapters,
  onOpenUsage,
}: {
  bookId: string;
  run: AgenticRunStatus | null;
  status: AgenticRunStatus["status"] | null;
  progress: AgenticProgress | null;
  error: string | null;
  units: BookUnits;
  chapters: readonly ChapterRange[];
  onOpenUsage: () => void;
}) {
  const live = status === "queued" || status === "running";
  const usage = useAgenticUsage(bookId, run?.agentic_run_id ?? null, live, run !== null);
  const chapterRows = chapterProgress(chapters, progress?.processed_pages ?? []);
  const chapterSummary = chapterProgressSummary(chapters, progress?.processed_pages ?? []);
  const [mode, setMode] = useState<(typeof MODES)[number]["id"]>("graph");
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);
  const selectedNode = AGENTIC_NODES.find((node) => node.id === selectedNodeId);
  const runStatus = status ?? run?.status ?? "pending";
  const graphProgress = progress ?? EMPTY_AGENTIC_PROGRESS;
  const total = run?.expected_page_count ?? 0;
  const summary: AgenticRunSummary | null =
    run?.status === "done"
      ? {
          deliveredPages: run.delivered_page_count,
          reviewPages: run.review_page_count,
          unverifiedPages: run.unverified_page_count,
        }
      : null;
  const views = agenticNodeViews(graphProgress, runStatus, total, units.plural, summary);
  const graphQuery = useAgenticGraph(mode === "langgraph");
  const caption = agenticBoardCaption(
    graphProgress,
    runStatus,
    run !== null,
    graphProgress.processed_pages.length,
    total,
    units.plural,
  );

  return (
    <div className="grid grid-cols-1 gap-4 xl:grid-cols-[minmax(0,4fr)_minmax(0,1fr)]">
      <section
        aria-label="Agentic pipeline stages"
        className="min-w-0 rounded-md border border-border bg-card p-3"
      >
        <header className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <h3 className="text-sm font-medium">Agentic pipeline</h3>
            <p aria-live="polite" className="mt-1 text-xs text-muted-foreground">
              {caption}
            </p>
          </div>
        </header>
        {error ? (
          <p role="alert" className="mt-3 break-words text-sm text-destructive-text">
            Agentic processing failed — {error}
          </p>
        ) : null}
        <div className="mt-3 grid min-w-0 grid-cols-1 gap-3 xl:grid-cols-[minmax(0,3fr)_minmax(16rem,1fr)]">
          <div className="min-w-0">
            {mode === "graph" ? (
              <AgenticFlow views={views} onOpenNode={setSelectedNodeId} />
            ) : mode === "list" ? (
              <AgenticNodeList rows={views} onOpen={setSelectedNodeId} />
            ) : graphQuery.data ? (
              <MermaidTopology title="Ingestion Agentic AI" source={graphQuery.data.graph_mermaid} />
            ) : (
              <p role="status" className="text-xs text-muted-foreground">
                {graphQuery.isPending
                  ? "Loading compiled LangGraph topology…"
                  : "Compiled LangGraph topology is unavailable."}
              </p>
            )}
          </div>
          <div className="flex min-w-0 flex-col">
            {/* Always available: with no run, or after its data was wiped, the sheet explains that there are no counters. */}
            <div className="mb-3 flex min-w-0 flex-col items-stretch gap-2">
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
            {/* From xl the box is as tall as the graph beside it and nothing more: its content is taken out of flow
             * (`absolute inset-0`), so a long list scrolls inside it instead of stretching the row. */}
            <section
              aria-label="Chapter progress"
              className="relative min-w-0 rounded-sm border border-border bg-background xl:min-h-72 xl:flex-1"
            >
              <div className="flex min-h-0 flex-col p-3 xl:absolute xl:inset-0">
                <header className="flex flex-wrap items-baseline justify-between gap-2">
                  <h4 className="text-sm font-medium">Chapter progress</h4>
                  {chapterSummary ? (
                    <span className="text-xs tabular-nums text-muted-foreground">
                      {chapterSummary.completed} of {chapterSummary.total} complete
                    </span>
                  ) : null}
                </header>
                {chapterRows.length > 0 ? (
                  <ol
                    aria-label="Chapter progress entries"
                    // biome-ignore lint/a11y/noNoninteractiveTabindex: a long chapter list scrolls, so keyboard users must be able to focus it to scroll it (WCAG 2.1.1)
                    tabIndex={0}
                    className="mt-3 flex max-h-[34rem] min-h-0 flex-col gap-3 overflow-y-auto xl:max-h-none xl:flex-1"
                  >
                    {chapterRows.map((chapter) => {
                      const percent = agenticPercent(chapter.processed, chapter.total);
                      return (
                        <li key={`${chapter.first_page}:${chapter.last_page}`} className="min-w-0">
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
                  <p className="mt-3 text-xs text-muted-foreground">
                    Chapter ranges are not available for this book.
                  </p>
                )}
              </div>
            </section>
          </div>
        </div>
        {progress && progress.audit_warning_pages.length > 0 ? (
          <p className="mt-3 text-xs text-warning-text">
            Audit warnings · {progress.audit_warning_pages.length}{" "}
            {progress.audit_warning_pages.length === 1 ? "page" : "pages"} · Pages{" "}
            {progress.audit_warning_pages.join(", ")}
          </p>
        ) : null}
      </section>
      <PipelineUsageTotal
        usage={usage.data}
        hasRun={run !== null}
        loading={run !== null && usage.isPending}
        failed={run !== null && usage.isError}
      />
      {selectedNode ? (
        <AgenticNodeSheet
          node={selectedNode}
          status={agenticNodeStatus(graphProgress, selectedNode.id, runStatus)}
          caption={agenticNodeCaption(graphProgress, selectedNode.id, total, units.plural)}
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

const PENDING_NOTE: Record<AgenticNodeStatus, string | null> = {
  done: null,
  failed: "This node failed before it finished. The run's error is shown above the graph.",
  running: "Running — its results appear here the moment the node commits.",
  pending: "This node has not run yet.",
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
        <section aria-label="What this node does" className="flex flex-col gap-3">
          <p className="text-sm">{details.subtitle}</p>
          <ul className="list-disc space-y-1 pl-5 text-sm text-muted-foreground">
            {details.actions.map((action) => (
              <li key={action}>{action}</li>
            ))}
          </ul>
          <AgenticNodeDetailsList resources={details.resources} />
          <div className="grid grid-cols-[auto_1fr] items-baseline gap-x-3 gap-y-2 text-xs text-muted-foreground">
            <span>Reads</span>
            <AgenticPills items={details.reads} />
            <span>Writes</span>
            <AgenticPills items={details.writes} />
            <span>LLM</span>
            <span className="text-sm">{details.llm}</span>
          </div>
        </section>
        {note ? <p className="text-sm text-muted-foreground">{note}</p> : null}
        <UsageBreakdown usage={usage} nodeId={node.id} loading={loading} failed={failed} hasRun={hasRun} />
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
  live,
  onClose,
}: {
  open: boolean;
  bookId: string;
  /** Null before the first run, and again after its data was wiped: the sheet still opens and says so. */
  run: AgenticRunStatus | null;
  live: boolean;
  onClose: () => void;
}) {
  return open ? (
    <UsageSheet bookId={bookId} runId={run?.agentic_run_id ?? null} live={live} onClose={onClose} />
  ) : null;
}

function UsageSheet({
  bookId,
  runId,
  live,
  onClose,
}: {
  bookId: string;
  runId: string | null;
  live: boolean;
  onClose: () => void;
}) {
  const dialog = useModalDialog();
  const usage = useAgenticUsage(bookId, runId, live, runId !== null);

  return (
    <dialog
      ref={dialog}
      aria-labelledby="bd-agentic-usage-title"
      onCancel={(event) => {
        event.preventDefault();
        onClose();
      }}
      className="m-0 ml-auto h-dvh max-h-none w-full max-w-2xl overflow-y-auto border-l border-border bg-background p-0 text-foreground backdrop:bg-background/80"
    >
      <header className="sticky top-0 z-10 flex items-start gap-3 border-b border-border bg-background px-5 py-4">
        <div className="min-w-0 flex-1">
          <h2 id="bd-agentic-usage-title" className="text-lg font-semibold tracking-tight">
            Ingestion LLM usage
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
