"use client";

import { apiErrorMessage } from "@/shared/lib/api-error";
import type { StreamStatus } from "@/shared/sse";
import { ActionButton } from "../../components/action-button";
import type { ContentSelection } from "../../entities";
import type { DistillRunViewState } from "../domain/run-state";
import type { DistillRun } from "../entities";
import type { StudyViewId } from "../params";
import { ContentSelectionReview } from "./content-selection-review";
import { DistillRunPanel } from "./distill-run-panel";

/** Study separates the published guide from the editable source-page selection. */
export function DistillOutputPanel({
  bookId,
  agenticRunId,
  selection,
  selectionPending,
  selectionError,
  savingSelection,
  saveSelectionError,
  onRetrySelection,
  onSaveSelection,
  run,
  runState,
  studyView,
  streamStatus,
  runsPending,
  runsError,
  onRetryRuns,
}: {
  bookId: string;
  agenticRunId: string | null;
  selection: ContentSelection | null;
  selectionPending: boolean;
  selectionError: unknown;
  savingSelection: boolean;
  saveSelectionError: unknown;
  onRetrySelection: () => void;
  onSaveSelection: (
    expectedRevision: number,
    overrides: { page_number: number; disposition: "include" | "exclude" }[],
  ) => void;
  run: DistillRun | null;
  runState: DistillRunViewState | null;
  studyView: StudyViewId;
  streamStatus: StreamStatus;
  runsPending: boolean;
  runsError: unknown;
  onRetryRuns: () => void;
}) {
  const status = runState?.status ?? run?.status ?? null;
  const progress = runState?.progress ?? run?.progress ?? null;
  const guideAvailable = status === "done" && run !== null && runState !== null;

  if (runsPending) {
    return (
      <section
        aria-label="Study content"
        role="status"
        className="flex min-h-0 flex-1 items-center justify-center"
      >
        <p className="text-sm text-muted-foreground">Loading Study content…</p>
      </section>
    );
  }

  if (runsError) {
    return (
      <section
        aria-label="Study content"
        role="alert"
        className="flex min-h-0 flex-1 flex-col items-center justify-center gap-3 p-4 text-sm"
      >
        <p className="text-destructive-text">
          Distill results could not be loaded — {apiErrorMessage(runsError)}
        </p>
        <ActionButton onClick={onRetryRuns}>Try again</ActionButton>
      </section>
    );
  }

  if (studyView === "selection") {
    return (
      <section aria-label="Page selection" className="flex min-h-0 min-w-0 flex-1 flex-col">
        <div className="min-h-0 flex-1 overflow-y-auto">
          <div className="mx-auto w-full max-w-screen-2xl py-4">
            {status === "queued" || status === "running" ? (
              <p role="status" className="mb-3 text-sm text-muted-foreground">
                {status === "queued"
                  ? "Your Learning Guide is queued."
                  : "Your Learning Guide is being prepared."}
                {progress && progress.total_units > 0
                  ? ` ${progress.completed_units.toLocaleString()} of ${progress.total_units.toLocaleString()} learning units complete.`
                  : " Distill is processing the selected source pages."}
              </p>
            ) : null}
            {status === "failed" ? (
              <p role="alert" className="mb-3 text-sm text-destructive-text">
                {runState?.error ?? run?.error ?? "The latest Distill run failed."}
              </p>
            ) : null}
            {status === "cancelled" ? (
              <p role="status" className="mb-3 text-sm text-muted-foreground">
                The latest Distill run was stopped.
              </p>
            ) : null}
            <p className="mb-3 text-sm text-muted-foreground">
              {guideAvailable
                ? "Changes here apply to future runs. The published Learning Guide keeps the page selection used for its run."
                : "Review which extracted pages Distill should use. Original book pages remain in the Pages tab."}
            </p>
            <ContentSelectionReview
              bookId={bookId}
              agenticRunId={agenticRunId}
              selection={selection}
              pending={selectionPending}
              error={selectionError}
              saving={savingSelection}
              saveError={saveSelectionError}
              onRetry={onRetrySelection}
              onSave={onSaveSelection}
            />
          </div>
        </div>
      </section>
    );
  }

  if (guideAvailable && run && runState) {
    return <DistillRunPanel bookId={bookId} run={run} state={runState} streamStatus={streamStatus} />;
  }

  return (
    <section
      aria-label="Learning Guide status"
      className="flex min-h-0 flex-1 flex-col items-center justify-center p-6 text-center"
    >
      <div aria-live="polite" className="max-w-prose">
        <h2 className="text-lg font-semibold tracking-tight">
          {status === "queued" || status === "running"
            ? status === "queued"
              ? "Learning Guide queued"
              : "Learning Guide in progress"
            : status === "failed"
              ? "Learning Guide run failed"
              : status === "cancelled"
                ? "Learning Guide run stopped"
                : "Learning Guide not available yet"}
        </h2>
        <p className="mt-2 text-sm text-muted-foreground">
          {status === "queued" || status === "running"
            ? progress && progress.total_units > 0
              ? `${progress.completed_units.toLocaleString()} of ${progress.total_units.toLocaleString()} learning units complete.`
              : "Distill is processing the selected source pages."
            : status === "failed"
              ? (runState?.error ?? run?.error ?? "The latest Distill run failed.")
              : status === "cancelled"
                ? "Open Distill to resume the saved run."
                : "Complete Ingestion and start a Distill run to create a Learning Guide."}
        </p>
        {streamStatus === "reconnecting" ? (
          <p className="mt-2 text-xs text-warning-text">Connection lost — reconnecting to run updates…</p>
        ) : null}
      </div>
    </section>
  );
}
