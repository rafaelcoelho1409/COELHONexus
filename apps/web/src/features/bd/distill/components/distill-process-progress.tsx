"use client";

import { Info } from "lucide-react";
import type { ReactNode } from "react";
import { ActionButton } from "../../components/action-button";
import type { RagIndexStatus } from "../../rag/index-entities";
import type { DistillRun } from "../entities";

export function DistillProcessProgress({
  status,
  progress,
  statusText,
  error,
  controls,
  onOpenInfo,
}: {
  status: DistillRun["status"] | null;
  progress: DistillRun["progress"] | null;
  statusText: string;
  error: string | null;
  controls: ReactNode;
  onOpenInfo: () => void;
}) {
  const total = progress?.total_units ?? 0;
  const completed = progress ? Math.min(progress.completed_units, total) : 0;
  const percent = total > 0 ? Math.round((completed / total) * 100) : status === "done" ? 100 : 0;

  return (
    <ProgressCard
      title="Distill Agentic AI"
      status={statusText}
      detail=""
      percent={percent}
      progressLabel="Learning units"
      progressValue={
        total > 0
          ? `${completed.toLocaleString()} / ${total.toLocaleString()} · ${percent}%`
          : "Waiting for unit totals"
      }
      primaryMetric={[
        "Completed units",
        total > 0 ? `${completed.toLocaleString()} / ${total.toLocaleString()}` : "—",
      ]}
      secondaryMetric={[
        "Chapters",
        progress?.total_chapters ? `${progress.completed_chapters} / ${progress.total_chapters}` : "—",
      ]}
      error={error}
      controls={controls}
      onOpenInfo={onOpenInfo}
      infoLabel="Open learning progress"
    />
  );
}

export function RagProcessProgress({
  index,
  distillComplete,
  isPending,
  queryError,
  executionEnabled,
  capabilitiesPending,
  capabilityError,
  startPending,
  cancelPending,
  wipePending,
  onStart,
  onCancel,
  onWipe,
  onRetry,
  onOpenInfo,
}: {
  index: RagIndexStatus | null;
  distillComplete: boolean;
  isPending: boolean;
  queryError: string | null;
  executionEnabled: boolean;
  capabilitiesPending: boolean;
  capabilityError: string | null;
  startPending: boolean;
  cancelPending: boolean;
  wipePending: boolean;
  onStart: () => void;
  onCancel: () => void;
  onWipe: () => void;
  onRetry: () => void;
  onOpenInfo: () => void;
}) {
  const active = index?.status === "queued" || index?.status === "running";
  const resumable = index?.status === "failed" || index?.status === "cancelled";
  const hasRun = index !== null && index.status !== "not_started";
  const canStart =
    distillComplete && executionEnabled && !active && index?.status !== "done" && !isPending && !queryError;
  const stageProgress =
    index?.status === "done" || index?.current_stage === "complete"
      ? 100
      : index?.current_stage === "graph_materialization"
        ? 75
        : index?.current_stage === "vector_index"
          ? 45
          : index?.current_stage === "preparing"
            ? 15
            : index?.vector_status === "done"
              ? 60
              : 0;
  const statusText = !distillComplete
    ? "Waiting for Distill"
    : isPending
      ? "Loading index status"
      : queryError
        ? "Status unavailable"
        : index?.cancel_requested
          ? "Stopping"
          : index?.status === "not_started" || !index
            ? "Ready to run"
            : index.status === "running"
              ? "Indexing"
              : index.status === "done"
                ? "Complete"
                : index.status === "failed"
                  ? "Failed"
                  : index.status === "cancelled"
                    ? "Stopped"
                    : "Queued";
  const detail = !distillComplete
    ? ""
    : index?.current_stage === "graph_materialization"
      ? "Linking book knowledge"
      : index?.current_stage === "vector_index"
        ? "Building the vector index"
        : index?.current_stage === "preparing"
          ? "Pinning book sources"
          : statusText;
  const progressValue = !distillComplete
    ? "No index run yet"
    : index?.status === "done"
      ? "All indexing stages complete"
      : index?.status === "not_started" || !index
        ? "No index run yet"
        : `${index.point_count.toLocaleString()} passages · ${index.graph_node_count.toLocaleString()} graph nodes`;

  return (
    <ProgressCard
      title="Agentic RAG"
      status={statusText}
      detail={detail}
      percent={stageProgress}
      progressLabel="Indexing stages"
      progressValue={progressValue}
      primaryMetric={["Source pages", index?.source_page_count.toLocaleString() ?? "—"]}
      secondaryMetric={[
        "Passages / graph nodes",
        index ? `${index.point_count.toLocaleString()} / ${index.graph_node_count.toLocaleString()}` : "—",
      ]}
      error={queryError ?? index?.error ?? capabilityError}
      controls={
        <>
          <RagAction
            label={resumable ? "Resume" : "Start"}
            ariaLabel={resumable ? "Resume Agentic RAG" : "Start Agentic RAG"}
            onClick={onStart}
            disabled={!canStart || startPending || cancelPending || wipePending || capabilitiesPending}
            busy={startPending}
          />
          <RagAction
            label={index?.cancel_requested || cancelPending ? "Stopping…" : "Stop"}
            ariaLabel="Stop Agentic RAG"
            onClick={onCancel}
            disabled={!active || index?.cancel_requested === true || cancelPending}
            busy={cancelPending || index?.cancel_requested === true}
          />
          <RagAction
            label={wipePending ? "Wiping…" : "Wipe"}
            ariaLabel="Wipe Agentic RAG index"
            onClick={onWipe}
            disabled={!hasRun || active || wipePending || startPending || cancelPending}
            busy={wipePending}
          />
          {queryError ? (
            <ActionButton className="col-span-3 w-full justify-center" onClick={onRetry}>
              Retry status
            </ActionButton>
          ) : null}
        </>
      }
      onOpenInfo={onOpenInfo}
      infoLabel="Open Agentic RAG stage details"
    />
  );
}

function RagAction({
  label,
  ariaLabel,
  onClick,
  disabled,
  busy,
}: {
  label: string;
  ariaLabel: string;
  onClick: () => void;
  disabled: boolean;
  busy: boolean;
}) {
  return (
    <ActionButton
      type="button"
      className="w-full min-w-0 justify-center px-1"
      onClick={onClick}
      disabled={disabled}
      aria-busy={busy}
      aria-label={ariaLabel}
    >
      {label}
    </ActionButton>
  );
}

function ProgressCard({
  title,
  status,
  detail,
  percent,
  progressLabel,
  progressValue,
  primaryMetric,
  secondaryMetric,
  error,
  controls,
  onOpenInfo,
  infoLabel,
}: {
  title: string;
  status: string;
  detail: string;
  percent: number;
  progressLabel: string;
  progressValue: string;
  primaryMetric: [string, string];
  secondaryMetric: [string, string];
  error?: string | null;
  controls: ReactNode;
  onOpenInfo: () => void;
  infoLabel: string;
}) {
  return (
    <section aria-label={`${title} progress`} className="min-w-0 rounded-md border border-border bg-card p-3">
      <header className="flex flex-wrap items-center justify-between gap-2">
        <h3 className="text-sm font-medium">{title}</h3>
        <div className="flex items-center gap-2">
          <span aria-live="polite" className="text-xs text-muted-foreground">
            {status}
          </span>
          <ActionButton
            iconOnly
            type="button"
            onClick={onOpenInfo}
            aria-haspopup="dialog"
            aria-label={infoLabel}
            title={infoLabel.replace(/^Open /, "")}
          >
            <Info aria-hidden />
          </ActionButton>
        </div>
      </header>
      <fieldset className="mt-2 grid min-w-0 grid-cols-3 gap-2 border-0 p-0">
        <legend className="sr-only">{title} controls</legend>
        {controls}
      </fieldset>
      <div className="mt-3 flex items-baseline justify-between gap-2 text-xs">
        <span className="font-medium">{progressLabel}</span>
        <span className="text-right tabular-nums text-muted-foreground">{progressValue}</span>
      </div>
      <div
        role="progressbar"
        aria-label={`${title} ${progressLabel.toLowerCase()}`}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={percent}
        aria-valuetext={`${progressValue}; ${percent}%`}
        className="mt-1 h-1.5 w-full overflow-hidden rounded-sm bg-muted"
      >
        <div className="h-full bg-primary" style={{ width: `${percent}%` }} />
      </div>
      <dl className="mt-2 grid grid-cols-2 gap-2 text-xs">
        <Metric label={primaryMetric[0]} value={primaryMetric[1]} />
        <Metric label={secondaryMetric[0]} value={secondaryMetric[1]} />
      </dl>
      {detail ? <p className="mt-2 text-xs text-muted-foreground">{detail}</p> : null}
      {error ? (
        <p role="alert" className="mt-2 break-words text-xs text-destructive-text">
          {error}
        </p>
      ) : null}
    </section>
  );
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div className="min-w-0 rounded-sm border border-border bg-background px-2.5 py-1.5">
      <dt className="text-muted-foreground">{label}</dt>
      <dd className="break-words font-medium tabular-nums text-foreground">{value}</dd>
    </div>
  );
}
