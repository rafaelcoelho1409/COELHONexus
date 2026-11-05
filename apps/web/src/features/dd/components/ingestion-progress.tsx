import { Check } from "lucide-react";

import { ingestionPercent } from "../domain/ingestion-progress";
import { ActionButton } from "./action-button";

function SuccessMark() {
  return (
    <span
      role="img"
      aria-label="Completed successfully"
      title="Completed successfully"
      className="inline-flex text-success-text"
    >
      <Check className="size-4" aria-hidden="true" />
    </span>
  );
}

function ProgressBar({
  label,
  current,
  total,
  valueText,
  indeterminate,
}: {
  label: string;
  current: number;
  total: number;
  valueText: string;
  indeterminate: boolean;
}) {
  const percent = ingestionPercent(current, total);
  return (
    <div
      role="progressbar"
      aria-label={label}
      aria-valuemin={0}
      aria-valuemax={total > 0 ? total : 100}
      aria-valuenow={total > 0 ? Math.min(current, total) : 0}
      aria-valuetext={valueText}
      className="mt-2 h-1.5 w-full overflow-hidden rounded-sm bg-muted"
    >
      <div
        className={`h-full bg-primary ${indeterminate && percent === null ? "w-1/3 animate-pulse" : ""}`}
        style={percent === null ? undefined : { width: `${percent}%` }}
      />
    </div>
  );
}

/** How far the documentation extraction is, with the run's Start, Stop and Wipe. The numbers sit outside the live region
 * (they change on every page); only the status and the notes are announced. */
export function ExtractionProgress({
  statusText,
  current,
  total,
  tierText,
  lastUrl,
  successful,
  live,
  extracting,
  ingested,
  retryable,
  stopping,
  startPending,
  wipePending,
  statusUnavailable,
  cancelError,
  finishedText,
  onStart,
  onStop,
  onWipe,
  onDismiss,
}: {
  statusText: string;
  current: number;
  total: number;
  tierText: string | null;
  lastUrl: string | null;
  successful: boolean;
  live: boolean;
  extracting: boolean;
  ingested: boolean;
  retryable: boolean;
  stopping: boolean;
  startPending: boolean;
  wipePending: boolean;
  statusUnavailable: boolean;
  cancelError: string | null;
  finishedText: string | null;
  onStart: () => void;
  onStop: () => void;
  onWipe: () => void;
  onDismiss?: () => void;
}) {
  const percent = ingestionPercent(current, total);

  return (
    <section
      aria-label="Documentation extraction progress"
      className="min-w-0 rounded-md border border-border bg-card p-3"
    >
      <div className="flex min-h-9 flex-wrap items-center justify-between gap-2">
        <h3 className="flex items-center gap-1.5 text-sm font-medium">
          Documentation extraction
          {successful ? <SuccessMark /> : null}
        </h3>
        <div className="flex flex-wrap items-center justify-end gap-2">
          <span aria-live="polite" className="text-xs text-muted-foreground">
            {statusText}
          </span>
          <div className="flex flex-wrap items-center justify-end gap-1.5">
            <ActionButton
              onClick={onStart}
              disabled={live || startPending || ingested}
              aria-busy={startPending}
              aria-label={retryable ? "Retry documentation extraction" : "Start documentation extraction"}
              title={
                live
                  ? "Documentation extraction is already running."
                  : ingested
                    ? "Use Refresh in the framework menu to ingest this documentation again."
                    : undefined
              }
            >
              {startPending ? "Starting…" : retryable ? "Retry" : "Start"}
            </ActionButton>
            <ActionButton
              onClick={onStop}
              disabled={!extracting || stopping}
              aria-busy={stopping}
              aria-label="Stop documentation extraction"
              title={extracting ? undefined : "Documentation extraction is not running."}
            >
              {stopping ? "Stopping…" : "Stop"}
            </ActionButton>
            <ActionButton
              onClick={onWipe}
              disabled={live || wipePending || !ingested}
              aria-busy={wipePending}
              aria-label="Wipe ingested documentation"
              title={
                live
                  ? "Stop the ingestion and wait for it to finish before wiping."
                  : ingested
                    ? undefined
                    : "There is no ingested documentation to wipe."
              }
            >
              {wipePending ? "Wiping…" : "Wipe"}
            </ActionButton>
          </div>
        </div>
      </div>
      <ProgressBar
        label="Pages extracted"
        current={current}
        total={total}
        valueText={total > 0 ? `${Math.min(current, total)} of ${total} pages` : "not started"}
        indeterminate={extracting}
      />
      <p className="mt-1.5 flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-muted-foreground">
        <span className="tabular-nums">
          {current} of {total > 0 ? total : "?"} pages
          {percent !== null ? ` · ${percent}%` : ""}
        </span>
        {tierText ? <span>{tierText}</span> : null}
      </p>
      {lastUrl ? <p className="mt-1 break-all font-mono text-xs text-muted-foreground">{lastUrl}</p> : null}
      {statusUnavailable ? (
        <p role="status" className="mt-1 text-xs text-warning-text">
          Run progress is unavailable. The saved library remains available in Materials.
        </p>
      ) : null}
      {cancelError ? (
        <p role="alert" className="mt-1 break-words text-xs text-destructive-text">
          {cancelError}
        </p>
      ) : null}
      {finishedText && onDismiss ? (
        <div className="mt-2 flex flex-wrap items-center justify-between gap-2">
          <p role="status" aria-live="polite" className="text-xs text-muted-foreground">
            {finishedText}
          </p>
          <ActionButton onClick={onDismiss}>Dismiss run</ActionButton>
        </div>
      ) : null}
      <p className="mt-2 text-xs text-muted-foreground">
        Wipe removes the ingested pages and everything built on them. The framework stays in the catalog.
      </p>
    </section>
  );
}

/** How far the Agentic AI normalization is, with controls for the saved page set. */
export function AgenticProgress({
  statusText,
  current,
  total,
  audited,
  successful,
  live,
  stopping,
  preparing,
  error,
  active,
  actionPending,
  cancelPending,
  wipePending,
  hasRun,
  ready,
  retryable,
  cancelError,
  onStart,
  onResume,
  onStop,
  onWipe,
}: {
  statusText: string;
  current: number;
  total: number;
  audited: number;
  successful: boolean;
  live: boolean;
  stopping: boolean;
  preparing: boolean;
  error: string | null;
  active: boolean;
  actionPending: boolean;
  cancelPending: boolean;
  wipePending: boolean;
  hasRun: boolean;
  ready: boolean;
  retryable: boolean;
  cancelError: string | null;
  onStart: () => void;
  onResume: () => void;
  onStop: () => void;
  onWipe: () => void;
}) {
  const percent = ingestionPercent(current, total);

  return (
    <section aria-label="Agentic AI progress" className="min-w-0 rounded-md border border-border bg-card p-3">
      <div className="flex min-h-9 flex-wrap items-center justify-between gap-2">
        <h3 className="flex items-center gap-1.5 text-sm font-medium">
          Agentic AI normalization
          {successful ? <SuccessMark /> : null}
        </h3>
        <div className="flex flex-wrap items-center justify-end gap-1.5">
          <span aria-live="polite" className="text-xs text-muted-foreground">
            {statusText}
          </span>
          <ActionButton
            onClick={retryable ? onResume : onStart}
            disabled={actionPending || active || live || (!ready && !retryable)}
            aria-busy={actionPending}
            aria-label={retryable ? "Resume Agentic AI" : "Start Agentic AI"}
            title={
              !ready && !retryable
                ? "Complete documentation extraction before starting Agentic AI."
                : active
                  ? "An Agentic AI run is already active."
                  : undefined
            }
          >
            {actionPending ? (retryable ? "Resuming…" : "Starting…") : retryable ? "Resume" : "Start"}
          </ActionButton>
          <ActionButton
            onClick={onStop}
            disabled={!active || cancelPending || stopping}
            aria-busy={cancelPending || stopping}
            aria-label="Stop Agentic AI"
          >
            {cancelPending || stopping ? "Stopping…" : "Stop"}
          </ActionButton>
          <ActionButton
            onClick={onWipe}
            disabled={!hasRun || live || wipePending}
            aria-busy={wipePending}
            aria-label="Wipe Agentic AI"
            title={
              !hasRun
                ? "There are no Agentic AI artifacts to wipe."
                : live
                  ? "Wait for the current run to finish before wiping."
                  : undefined
            }
          >
            {wipePending ? "Wiping…" : "Wipe"}
          </ActionButton>
        </div>
      </div>
      <ProgressBar
        label="AI-normalized pages"
        current={current}
        total={total}
        valueText={total > 0 ? `${Math.min(current, total)} of ${total} pages AI normalized` : "not started"}
        indeterminate={false}
      />
      <p className="mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted-foreground">
        <span className="tabular-nums">
          {Math.min(current, total || current)} of {total > 0 ? total : "?"} pages normalized
          {percent !== null ? ` · ${percent}%` : ""}
        </span>
        {total > 0 ? <span className="tabular-nums">{audited} audited</span> : null}
      </p>
      {preparing ? (
        <p role="status" className="mt-1 text-xs text-muted-foreground">
          Preparing page boundaries for Agentic AI.
        </p>
      ) : null}
      {error ? (
        <p role="alert" className="mt-1 break-words text-xs text-destructive-text">
          {error}
        </p>
      ) : null}
      {cancelError ? (
        <p role="alert" className="mt-1 break-words text-xs text-destructive-text">
          {cancelError}
        </p>
      ) : null}
      {stopping ? (
        <p aria-live="polite" className="mt-1 text-xs text-muted-foreground">
          Stop requested; active page work is draining before the run ends.
        </p>
      ) : active ? (
        <p className="mt-1 text-xs text-muted-foreground">
          Stop takes effect at a safe page or graph-stage boundary. Wipe becomes available after the run
          stops.
        </p>
      ) : (
        <p className="mt-1 text-xs text-muted-foreground">
          Agentic AI starts automatically after extraction. Start runs a new candidate against saved pages.
        </p>
      )}
    </section>
  );
}
