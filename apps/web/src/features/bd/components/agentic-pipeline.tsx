"use client";

import { ChartNoAxesColumn, Check, X } from "lucide-react";
import dynamic from "next/dynamic";
import { useState } from "react";
import type { StreamStatus } from "@/shared/sse";
import { Button } from "@/shared/ui/button";
import {
  AGENTIC_NODES,
  agenticNodeCaption,
  agenticNodeStatus,
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
  qualityStatus,
  deliveredPageCount,
  discardedBlankPageCount,
  reviewPageCount,
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
          <Button
            variant="outline"
            size="default"
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
          </Button>
          <Button
            variant="outline"
            size="default"
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
          </Button>
          <Button
            variant="destructive"
            size="default"
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
          </Button>
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
      <p className="mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted-foreground">
        <span className="tabular-nums">
          {completed} of {total > 0 ? total : "?"} {units.plural} processed
          {percent !== null ? ` · ${percent}%` : ""}
        </span>
        {progress ? <span className="tabular-nums">{progress.audited_pages.length} audited</span> : null}
        {progress && progress.audit_warning_pages.length > 0 ? (
          <span className="tabular-nums text-warning-text">
            {progress.audit_warning_pages.length}{" "}
            {progress.audit_warning_pages.length === 1 ? "page needs" : "pages need"} review
          </span>
        ) : null}
        {chapterSummary ? (
          <span className="tabular-nums">
            {chapterSummary.completed} of {chapterSummary.total} chapters
          </span>
        ) : null}
        {progress && progress.active_audit_pages.length > 0 ? (
          <span>{progress.active_audit_pages.length} auditing</span>
        ) : null}
        {progress && progress.active_repair_pages.length > 0 ? (
          <span>{progress.active_repair_pages.length} repairing</span>
        ) : null}
      </p>
      {qualityStatus === "complete_with_review" ? (
        <p className="mt-1 text-xs text-warning-text">
          Review required · {reviewPageCount} {reviewPageCount === 1 ? "page" : "pages"} · {deliveredPageCount} delivered · {discardedBlankPageCount} blank excluded
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

  return (
    <div className="grid grid-cols-1 gap-4 xl:grid-cols-[minmax(0,4fr)_minmax(0,1fr)]">
      <section
        aria-label="Agentic pipeline stages"
        className="min-w-0 rounded-md border border-border bg-card p-3"
      >
        <header className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <h3 className="text-sm font-medium">Agentic pipeline</h3>
            <p className="mt-1 text-xs text-muted-foreground">
              {run
                ? `Run ${runStatus} · ${progress?.processed_pages.length ?? 0} of ${run.expected_page_count} ${units.plural} processed`
                : "Agentic AI is ready to run after extraction."}
            </p>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            {run ? (
              <Button variant="outline" size="default" onClick={onOpenUsage} aria-haspopup="dialog">
                <ChartNoAxesColumn aria-hidden />
                LLM usage
              </Button>
            ) : null}
            <fieldset className="m-0 flex border-0 p-0">
              <legend className="sr-only">Pipeline shown as</legend>
              {MODES.map((item) => (
                <button
                  key={item.id}
                  type="button"
                  aria-pressed={mode === item.id}
                  onClick={() => setMode(item.id)}
                  className={`min-h-9 border px-3 text-xs first:rounded-l-sm last:rounded-r-sm ${
                    mode === item.id
                      ? "border-primary bg-primary/10 text-primary-text"
                      : "-ml-px border-border text-muted-foreground hover:text-foreground"
                  }`}
                >
                  {item.label}
                </button>
              ))}
            </fieldset>
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
              <AgenticFlow
                progress={graphProgress}
                status={runStatus}
                total={run?.expected_page_count ?? 0}
                unitPlural={units.plural}
                onOpenNode={setSelectedNodeId}
              />
            ) : (
              <ol aria-label="Agentic pipeline nodes" className="flex flex-col gap-1.5">
                {AGENTIC_NODES.map((node) => {
                  const nodeStatus = agenticNodeStatus(graphProgress, node.id, runStatus);
                  const caption = agenticNodeCaption(
                    graphProgress,
                    node.id,
                    run?.expected_page_count ?? 0,
                    units.plural,
                  );
                  return (
                    <li key={node.id} data-status={nodeStatus}>
                      <button
                        type="button"
                        aria-haspopup="dialog"
                        onClick={() => setSelectedNodeId(node.id)}
                        className={`flex min-h-14 w-full flex-col justify-center rounded-sm border bg-background px-3 py-2 text-left hover:bg-muted focus-visible:outline-2 focus-visible:outline-ring ${
                          nodeStatus === "running"
                            ? "border-running"
                            : nodeStatus === "failed"
                              ? "border-destructive"
                              : "border-border"
                        }`}
                      >
                        <span className="flex flex-wrap items-center justify-between gap-2">
                          <span className="text-sm font-medium">{node.label}</span>
                          <span className="text-xs text-muted-foreground">{nodeStatus}</span>
                        </span>
                        {caption ? (
                          <span className="mt-1 text-xs text-muted-foreground">{caption}</span>
                        ) : null}
                      </button>
                    </li>
                  );
                })}
              </ol>
            )}
          </div>
          <section
            aria-label="Chapter progress"
            className="min-w-0 rounded-sm border border-border bg-background p-3"
          >
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
                className="mt-3 flex max-h-[34rem] flex-col gap-3 overflow-y-auto"
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
          </section>
        </div>
        {progress && progress.audit_warning_pages.length > 0 ? (
          <p className="mt-3 text-xs text-warning-text">
            {progress.audit_warning_pages.length}{" "}
            {progress.audit_warning_pages.length === 1 ? "page needs" : "pages need"} review · Pages{" "}
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
          caption={agenticNodeCaption(
            graphProgress,
            selectedNode.id,
            run?.expected_page_count ?? 0,
            units.plural,
          )}
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

function AgenticNodeSheet({
  node,
  status,
  caption,
  usage,
  loading,
  failed,
  hasRun,
  onClose,
}: {
  node: (typeof AGENTIC_NODES)[number];
  status: ReturnType<typeof agenticNodeStatus>;
  caption: string | null;
  usage: AgenticUsage | undefined;
  loading: boolean;
  failed: boolean;
  hasRun: boolean;
  onClose: () => void;
}) {
  const dialog = useModalDialog();

  return (
    <dialog
      ref={dialog}
      aria-labelledby="bd-agentic-node-usage-title"
      onCancel={(event) => {
        event.preventDefault();
        onClose();
      }}
      className="m-0 ml-auto h-dvh max-h-none w-full max-w-2xl overflow-y-auto border-l border-border bg-background p-0 text-foreground backdrop:bg-background/80"
    >
      <header className="sticky top-0 z-10 flex items-start gap-3 border-b border-border bg-background px-5 py-4">
        <div className="min-w-0 flex-1">
          <h2 id="bd-agentic-node-usage-title" className="text-lg font-semibold tracking-tight">
            Ingestion LLM usage
          </h2>
          <p className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted-foreground">
            <span>{node.label}</span>
            <span>{status}</span>
            <span className="font-mono">ingestion · {node.id}</span>
            {caption ? <span>{caption}</span> : null}
          </p>
        </div>
        <Button variant="ghost" size="icon-lg" onClick={onClose} aria-label="Close node usage">
          <X aria-hidden />
        </Button>
      </header>
      <div className="flex flex-col gap-4 px-5 py-4">
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
  run: AgenticRunStatus;
  live: boolean;
  onClose: () => void;
}) {
  return open ? (
    <UsageSheet bookId={bookId} runId={run.agentic_run_id} live={live} onClose={onClose} />
  ) : null;
}

function UsageSheet({
  bookId,
  runId,
  live,
  onClose,
}: {
  bookId: string;
  runId: string;
  live: boolean;
  onClose: () => void;
}) {
  const dialog = useModalDialog();
  const usage = useAgenticUsage(bookId, runId, live, true);

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
        <Button variant="ghost" size="icon-lg" onClick={onClose} aria-label="Close usage">
          <X aria-hidden />
        </Button>
      </header>
      <div className="flex flex-col gap-4 px-5 py-4">
        <UsageBreakdown usage={usage.data} loading={usage.isPending} failed={usage.isError} hasRun />
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
        <fieldset className="m-0 flex flex-wrap items-center gap-2 border-0 p-0">
          <legend className="sr-only">Group usage by</legend>
          {USAGE_MODES.map((item) => (
            <button
              key={item.id}
              type="button"
              aria-pressed={mode === item.id}
              onClick={() => setMode(item.id)}
              className={`min-h-9 rounded-sm border px-3 text-xs ${
                mode === item.id
                  ? "border-primary bg-primary/10 text-primary-text"
                  : "border-border text-muted-foreground hover:text-foreground"
              }`}
            >
              {item.label}
            </button>
          ))}
        </fieldset>
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
        <p className="text-sm text-muted-foreground">Usage will appear after this node runs.</p>
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
