"use client";

import type { ReactNode } from "react";
import type { StreamStatus } from "@/shared/sse";
import { DISTILL_NODES } from "../domain/distill-progress";
import type { DistillReadiness } from "../domain/run-state";
import type { DistillRun } from "../entities";

export function DistillProgressPanel({
  run,
  status,
  progress,
  error,
  streamStatus,
  readiness,
  stopping,
  actions,
  capabilitiesPending,
  capabilitiesError,
  canExecute,
}: {
  run: DistillRun | null;
  status: DistillRun["status"] | null;
  progress: DistillRun["progress"] | null;
  error: string | null;
  streamStatus: StreamStatus;
  readiness: DistillReadiness;
  stopping: boolean;
  actions: ReactNode;
  capabilitiesPending: boolean;
  capabilitiesError: string | null;
  canExecute: boolean;
}) {
  const completed = progress?.completed_units ?? 0;
  const total = progress?.total_units ?? 0;
  const percent = total > 0 ? Math.round((Math.min(completed, total) / total) * 100) : null;
  const currentNode = DISTILL_NODES.find((node) => node.id === progress?.current_node);
  const statusText = stopping
    ? "Stopping"
    : status
      ? runStatusLabel(status)
      : readiness.ready
        ? "Ready to run"
        : "Waiting for source checks";
  const quality = run?.structural_quality;

  return (
    <section
      aria-label="Distill Agentic AI progress"
      className="min-w-0 rounded-md border border-border bg-card p-3"
    >
      <div className="flex min-w-0 flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
        <h3 className="text-sm font-medium">Distill Agentic AI</h3>
        <div className="flex min-w-0 flex-col items-start gap-2 sm:flex-row sm:items-center sm:justify-end">
          <span aria-live="polite" className="text-xs text-muted-foreground">
            {statusText}
          </span>
          <div className="flex flex-wrap items-center gap-2">{actions}</div>
        </div>
      </div>

      <div
        role="progressbar"
        aria-label="Learning units completed"
        aria-valuemin={0}
        aria-valuemax={total > 0 ? total : 100}
        aria-valuenow={total > 0 ? Math.min(completed, total) : 0}
        aria-valuetext={
          total > 0
            ? `${completed} of ${total} learning units completed`
            : "No learning unit progress reported"
        }
        className="mt-2 h-1.5 w-full overflow-hidden rounded-sm bg-muted"
      >
        <div className="h-full bg-primary" style={{ width: `${percent ?? 0}%` }} />
      </div>

      <dl className="mt-2 grid grid-cols-2 gap-2 text-xs sm:grid-cols-3">
        <ProgressMetric
          label="Learning units"
          value={
            total > 0
              ? `${completed.toLocaleString()} / ${total.toLocaleString()}${percent === null ? "" : ` · ${percent}%`}`
              : "—"
          }
        />
        <ProgressMetric
          label="Chapters"
          value={
            progress && progress.total_chapters > 0
              ? `${progress.completed_chapters.toLocaleString()} / ${progress.total_chapters.toLocaleString()}`
              : "—"
          }
        />
        <ProgressMetric
          label="Active stage"
          value={
            status === "running"
              ? (currentNode?.label ?? "Processing")
              : status
                ? runStatusLabel(status)
                : "Idle"
          }
        />
        <ProgressMetric
          label="Page coverage"
          value={quality ? `${Math.round(quality.page_coverage_rate * 100)}%` : "—"}
        />
        <ProgressMetric
          label="Valid source references"
          value={
            quality ? `${quality.valid_source_reference_count} / ${quality.source_reference_count}` : "—"
          }
        />
        <ProgressMetric
          label="Evidence checks"
          value={
            quality ? `${quality.supported_evidence_quote_count} / ${quality.evidence_quote_count}` : "—"
          }
        />
      </dl>

      {capabilitiesPending ? (
        <p role="status" className="mt-2 text-xs text-muted-foreground">
          Checking Distill availability…
        </p>
      ) : capabilitiesError ? (
        <p role="alert" className="mt-2 text-xs text-destructive-text">
          Distill availability could not be checked — {capabilitiesError}
        </p>
      ) : !canExecute ? (
        <p className="mt-2 text-xs text-muted-foreground">
          Distill runs are unavailable in this environment. Existing runs can still be stopped or wiped.
        </p>
      ) : null}

      {run?.review_required && status === "done" ? (
        <p className="mt-2 text-xs text-warning-text">
          Review required · {run.uncovered_pages.length} source pages are not covered by the Learning Guide.
        </p>
      ) : status === "done" && quality ? (
        <p className="mt-2 text-xs text-success-text">
          Quality checks complete · {quality.covered_substantive_pages} of {quality.substantive_page_count}{" "}
          substantive pages covered.
        </p>
      ) : null}

      {error ? (
        <p role="alert" className="mt-2 break-words text-xs text-destructive-text">
          {error}
        </p>
      ) : null}
      {streamStatus === "reconnecting" ? (
        <p role="status" className="mt-2 text-xs text-warning-text">
          Connection lost — reconnecting to run updates…
        </p>
      ) : null}
      {stopping ? (
        <p role="status" className="mt-2 text-xs text-muted-foreground">
          Stop requested; active work is draining before the run ends.
        </p>
      ) : status === "queued" || status === "running" ? (
        <p className="mt-2 text-xs text-muted-foreground">
          Stop takes effect at a safe graph-stage boundary. Wipe becomes available after the run stops.
        </p>
      ) : null}
    </section>
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

function runStatusLabel(status: DistillRun["status"]): string {
  if (status === "done") return "Complete";
  if (status === "queued") return "Queued";
  if (status === "running") return "Processing";
  if (status === "failed") return "Failed";
  return "Cancelled";
}
