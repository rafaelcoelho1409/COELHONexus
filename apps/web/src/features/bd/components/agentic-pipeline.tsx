"use client";

import { ChartNoAxesColumn, X } from "lucide-react";
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
} from "../domain/agentic-progress";
import type { BookUnits } from "../domain/books";
import type { AgenticProgress, AgenticRunStatus, ChapterRange } from "../entities";
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

export function AgenticProgressBar({
  status,
  progress,
  total,
  error,
  units,
  extractionDone,
  streamStatus,
  actionPending,
  onStart,
  onResume,
  onOpenPipeline,
  chapters,
}: {
  status: AgenticRunStatus["status"] | null;
  progress: AgenticProgress | null;
  total: number;
  error: string | null;
  units: BookUnits;
  extractionDone: boolean;
  streamStatus: StreamStatus;
  actionPending: boolean;
  onStart: () => void;
  onResume: () => void;
  onOpenPipeline: () => void;
  chapters: readonly ChapterRange[];
}) {
  const completed = progress?.processed_pages.length ?? 0;
  const percent = agenticPercent(completed, total);
  const chapterSummary = chapterProgressSummary(chapters, progress?.processed_pages ?? []);
  const statusText =
    status === null
      ? extractionDone
        ? "Ready to run"
        : "Not started"
      : status === "queued"
        ? "Queued"
        : status === "running"
          ? "Processing pages"
          : status;

  return (
    <div className="min-w-0">
      <div className="flex min-h-9 flex-wrap items-center justify-between gap-2">
        <h3 className="text-sm font-medium">Agentic AI</h3>
        <div className="flex flex-wrap items-center justify-end gap-2">
          <span aria-live="polite" className="text-xs text-muted-foreground">
            {statusText}
          </span>
          {(status === null && extractionDone) || status === "cancelled" ? (
            <Button variant="outline" size="default" onClick={onStart} disabled={actionPending}>
              {actionPending ? "Starting…" : "Run Agentic AI"}
            </Button>
          ) : status === "failed" ? (
            <>
              <Button variant="outline" size="default" onClick={onResume} disabled={actionPending}>
                {actionPending ? "Retrying…" : "Retry Agentic AI"}
              </Button>
              <Button variant="outline" size="default" onClick={onOpenPipeline}>
                View pipeline
              </Button>
            </>
          ) : status !== null ? (
            <Button variant="outline" size="default" onClick={onOpenPipeline}>
              View pipeline
            </Button>
          ) : null}
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
    </div>
  );
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
  const total = usage.data?.total;
  const rows = Object.entries(usage.data?.by_node ?? {}).filter(([, counts]) => counts.calls > 0);

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
        {total ? (
          <dl className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-5">
            {[
              ["Calls", total.calls],
              ["Input tokens", total.tokens_in],
              ["Output tokens", total.tokens_out],
              ["Reasoning tokens", total.reasoning_tokens],
              ["Errors", total.errors],
            ].map(([label, value]) => (
              <div key={label} className="rounded-sm border border-border px-3 py-2">
                <dt className="text-xs text-muted-foreground">{label}</dt>
                <dd className="text-lg font-semibold tabular-nums">{Number(value).toLocaleString()}</dd>
              </div>
            ))}
          </dl>
        ) : null}
        {usage.isError ? (
          <p role="alert" className="text-sm text-destructive-text">
            LLM usage could not be loaded.
          </p>
        ) : rows.length === 0 ? (
          <p className="text-sm text-muted-foreground">
            {usage.isPending ? "Loading LLM usage…" : "No LLM calls have been recorded yet."}
          </p>
        ) : (
          <section aria-label="Usage by node" className="rounded-sm border border-border">
            <table className="w-full table-fixed border-collapse text-xs sm:text-sm">
              <thead>
                <tr className="border-b border-border text-left text-xs text-muted-foreground">
                  <th scope="col" className="w-2/5 px-3 py-2 font-medium">
                    Node
                  </th>
                  <th scope="col" className="px-3 py-2 text-right font-medium">
                    Calls
                  </th>
                  <th scope="col" className="px-3 py-2 text-right font-medium">
                    Input
                  </th>
                  <th scope="col" className="px-3 py-2 text-right font-medium">
                    Output
                  </th>
                  <th scope="col" className="px-2 py-2 text-right font-medium sm:px-3">
                    Reasoning
                  </th>
                </tr>
              </thead>
              <tbody>
                {rows.map(([node, counts]) => (
                  <tr key={node} className="border-b border-border last:border-b-0">
                    <th scope="row" className="break-all px-3 py-1.5 text-left font-normal">
                      {node}
                    </th>
                    <td className="px-3 py-1.5 text-right tabular-nums">{counts.calls.toLocaleString()}</td>
                    <td className="px-3 py-1.5 text-right tabular-nums">
                      {counts.tokens_in.toLocaleString()}
                    </td>
                    <td className="px-3 py-1.5 text-right tabular-nums">
                      {counts.tokens_out.toLocaleString()}
                    </td>
                    <td className="px-2 py-1.5 text-right tabular-nums sm:px-3">
                      {counts.reasoning_tokens.toLocaleString()}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </section>
        )}
        {usage.data ? (
          <section aria-label="Usage by model" className="flex flex-col gap-2">
            <h3 className="text-sm font-medium">By model</h3>
            {Object.entries(usage.data.by_node).flatMap(([node, counts]) =>
              Object.entries(counts.by_model).map(([model, modelCounts]) => ({
                node,
                model,
                ...modelCounts,
              })),
            ).length > 0 ? (
              <ul className="flex flex-col gap-1 text-xs">
                {Object.entries(usage.data.by_node).flatMap(([node, counts]) =>
                  Object.entries(counts.by_model).map(([model, modelCounts]) => (
                    <li
                      key={`${node}:${model}`}
                      className="flex flex-wrap justify-between gap-x-3 gap-y-1 border-b border-border py-1.5"
                    >
                      <span className="min-w-0 break-all font-mono">{model}</span>
                      <span className="shrink-0 text-muted-foreground">
                        {node} · {modelCounts.calls.toLocaleString()} calls
                      </span>
                    </li>
                  )),
                )}
              </ul>
            ) : (
              <p className="text-sm text-muted-foreground">No model details have been recorded yet.</p>
            )}
          </section>
        ) : null}
      </div>
    </dialog>
  );
}

export function AgenticPipeline({
  open,
  onClose,
  bookId,
  run,
  status,
  progress,
  error,
  units,
  chapters,
}: {
  open: boolean;
  onClose: () => void;
  bookId: string;
  run: AgenticRunStatus;
  status: AgenticRunStatus["status"];
  progress: AgenticProgress;
  error: string | null;
  units: BookUnits;
  chapters: readonly ChapterRange[];
}) {
  return open ? (
    <AgenticPipelineSheet
      onClose={onClose}
      bookId={bookId}
      run={run}
      status={status}
      progress={progress}
      error={error}
      units={units}
      chapters={chapters}
    />
  ) : null;
}

function AgenticPipelineSheet({
  onClose,
  bookId,
  run,
  status,
  progress,
  error,
  units,
  chapters,
}: {
  onClose: () => void;
  bookId: string;
  run: AgenticRunStatus;
  status: AgenticRunStatus["status"];
  progress: AgenticProgress;
  error: string | null;
  units: BookUnits;
  chapters: readonly ChapterRange[];
}) {
  const dialog = useModalDialog();
  const [mode, setMode] = useState<(typeof MODES)[number]["id"]>("graph");
  const [usageOpen, setUsageOpen] = useState(false);
  const live = status === "queued" || status === "running";
  const total = run.expected_page_count;
  const usage = useAgenticUsage(bookId, run.agentic_run_id, live, true);
  const totalUsage = usage.data?.total;
  const chapterRows = chapterProgress(chapters, progress.processed_pages);
  const chapterSummary = chapterProgressSummary(chapters, progress.processed_pages);

  return (
    <dialog
      ref={dialog}
      aria-labelledby="bd-agentic-pipeline-title"
      onCancel={(event) => {
        event.preventDefault();
        onClose();
      }}
      className="m-0 ml-auto h-dvh max-h-none w-full max-w-3xl overflow-y-auto border-l border-border bg-background p-0 text-foreground backdrop:bg-background/80"
    >
      <header className="sticky top-0 z-10 flex items-start gap-3 border-b border-border bg-background px-5 py-4">
        <div className="min-w-0 flex-1">
          <h2 id="bd-agentic-pipeline-title" className="text-lg font-semibold tracking-tight">
            Agentic pipeline
          </h2>
          <p className="mt-1 text-sm text-muted-foreground">
            Page repairs appear as they complete; remaining pages stay on Docling output.
          </p>
        </div>
        <Button variant="ghost" size="icon-lg" onClick={onClose} aria-label="Close pipeline">
          <X aria-hidden />
        </Button>
      </header>

      <div className="flex flex-col gap-4 px-5 py-4">
        {error ? (
          <p role="alert" className="break-words text-sm text-destructive-text">
            Agentic processing failed — {error}
          </p>
        ) : null}

        <div className="flex flex-col gap-3">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <p className="text-xs text-muted-foreground">
              {progress.current_node
                ? `Current node: ${AGENTIC_NODES.find((node) => node.id === progress.current_node)?.label ?? progress.current_node}`
                : "Pipeline nodes"}
            </p>
            <div className="flex flex-wrap items-center gap-2">
              <Button variant="outline" size="default" onClick={() => setUsageOpen(true)}>
                <ChartNoAxesColumn aria-hidden />
                LLM Usage
              </Button>
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
          </div>

          {mode === "graph" ? (
            <AgenticFlow progress={progress} status={status} total={total} unitPlural={units.plural} />
          ) : (
            <ol aria-label="Agentic pipeline nodes" className="flex flex-col gap-1.5">
              {AGENTIC_NODES.map((node) => {
                const nodeStatus = agenticNodeStatus(progress, node.id, status);
                return (
                  <li
                    key={node.id}
                    data-status={nodeStatus}
                    className={`rounded-sm border bg-card px-3 py-2 ${nodeStatus === "running" ? "border-running" : nodeStatus === "failed" ? "border-destructive" : "border-border"}`}
                  >
                    <div className="flex flex-wrap items-center justify-between gap-2">
                      <span className="text-sm">{node.label}</span>
                      <span className="text-xs text-muted-foreground">{nodeStatus}</span>
                    </div>
                    {agenticNodeCaption(progress, node.id, total, units.plural) ? (
                      <p className="mt-1 text-xs text-muted-foreground">
                        {agenticNodeCaption(progress, node.id, total, units.plural)}
                      </p>
                    ) : null}
                  </li>
                );
              })}
            </ol>
          )}
        </div>

        {chapterRows.length > 0 && chapterSummary ? (
          <details className="rounded-sm border border-border px-3 py-2">
            <summary className="flex min-h-9 cursor-pointer items-center text-sm font-medium">
              Chapter progress · {chapterSummary.completed} of {chapterSummary.total} complete
            </summary>
            <ol aria-label="Chapter progress" className="flex flex-col gap-2 pb-2">
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
          </details>
        ) : null}

        {progress.audit_warning_pages.length > 0 ? (
          <details className="rounded-sm border border-warning px-3 py-2">
            <summary className="flex min-h-9 cursor-pointer items-center text-sm font-medium">
              Audit warnings · {progress.audit_warning_pages.length} pages
            </summary>
            <p className="pb-2 text-xs text-muted-foreground">
              The auditor was unavailable or returned evidence that could not be verified verbatim.
              Deterministic findings were kept, and these pages are flagged for review.
            </p>
            <p className="pb-2 text-xs tabular-nums text-muted-foreground">
              Pages: {progress.audit_warning_pages.join(", ")}
            </p>
          </details>
        ) : null}

        {totalUsage ? (
          <section aria-label="Pipeline total" className="rounded-md border border-border px-3 py-3">
            <h3 className="text-sm font-medium">{live ? "Pipeline usage so far" : "Pipeline total"}</h3>
            <dl className="mt-2 grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-5">
              {[
                ["Calls", totalUsage.calls],
                ["Input tokens", totalUsage.tokens_in],
                ["Output tokens", totalUsage.tokens_out],
                ["Reasoning tokens", totalUsage.reasoning_tokens],
                ["Errors", totalUsage.errors],
              ].map(([label, value]) => (
                <div key={label}>
                  <dt className="text-xs text-muted-foreground">{label}</dt>
                  <dd className="text-lg font-semibold tabular-nums">{Number(value).toLocaleString()}</dd>
                </div>
              ))}
            </dl>
          </section>
        ) : null}

        {usageOpen ? (
          <UsageSheet
            bookId={bookId}
            runId={run.agentic_run_id}
            live={live}
            onClose={() => setUsageOpen(false)}
          />
        ) : null}
      </div>
    </dialog>
  );
}
