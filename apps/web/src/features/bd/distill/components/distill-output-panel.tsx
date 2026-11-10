"use client";

import Link from "next/link";
import { useState } from "react";
import { apiErrorMessage } from "@/shared/lib/api-error";
import type { StreamStatus } from "@/shared/sse";
import { ActionButton } from "../../components/action-button";
import { ToggleGroup } from "../../components/toggle-group";
import type { ContentSelection } from "../../entities";
import type { DistillRunViewState } from "../domain/run-state";
import type { DistillRun } from "../entities";
import { distillHref } from "../params";
import { ContentSelectionReview } from "./content-selection-review";
import { DistillRunPanel } from "./distill-run-panel";

const STUDY_MODES = [
  { id: "guide", label: "Learning Guide" },
  { id: "selection", label: "Page selection" },
] as const;
type StudyMode = (typeof STUDY_MODES)[number]["id"];

/** Study displays Distill's published learning material; it never duplicates the source-page reader. */
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
  streamStatus: StreamStatus;
  runsPending: boolean;
  runsError: unknown;
  onRetryRuns: () => void;
}) {
  const [mode, setMode] = useState<StudyMode | null>(null);
  const status = runState?.status ?? run?.status ?? null;
  const progress = runState?.progress ?? run?.progress ?? null;
  const guideAvailable = status === "done" && run !== null && runState !== null;
  const visibleMode = guideAvailable ? (mode ?? "guide") : "selection";

  return (
    <section aria-label="Distilled book content" className="min-w-0">
      {runsPending ? (
        <p
          role="status"
          className="rounded-md border border-border bg-card p-4 text-sm text-muted-foreground"
        >
          Loading Distill results…
        </p>
      ) : runsError ? (
        <section role="alert" className="rounded-md border border-border bg-card p-4 text-sm">
          <p className="text-destructive-text">
            Distill results could not be loaded — {apiErrorMessage(runsError)}
          </p>
          <div className="mt-3">
            <ActionButton onClick={onRetryRuns}>Try again</ActionButton>
          </div>
        </section>
      ) : (
        <>
          <header className="flex flex-wrap items-end justify-between gap-3 rounded-md border border-border bg-card p-3">
            <div className="min-w-0">
              <p className="mono-label">Study · Distilled content</p>
              <h2 className="mt-1 text-lg font-semibold tracking-tight">
                {visibleMode === "guide" ? "Learning Guide" : "Page selection"}
              </h2>
            </div>
            <div className="flex min-w-0 flex-wrap items-center justify-end gap-2">
              {guideAvailable ? (
                <ToggleGroup
                  legend="Study content"
                  options={STUDY_MODES}
                  value={visibleMode}
                  onChange={setMode}
                />
              ) : null}
              <Link
                href={distillHref(bookId, run?.run_id)}
                className="inline-flex min-h-10 shrink-0 items-center justify-center rounded-sm border border-border bg-background px-3 text-sm font-medium hover:bg-muted"
              >
                Distill Agents
              </Link>
            </div>
          </header>

          {visibleMode === "guide" && guideAvailable && run && runState ? (
            <DistillRunPanel bookId={bookId} run={run} state={runState} streamStatus={streamStatus} />
          ) : (
            <div className="mt-4 min-w-0 rounded-md border border-border bg-card p-4 sm:p-6">
              {status === "queued" || status === "running" ? (
                <div role="status" className="mb-4 border-b border-border pb-3 text-sm">
                  <p className="font-medium">
                    {status === "queued"
                      ? "Your Learning Guide is queued"
                      : "Your Learning Guide is being prepared"}
                  </p>
                  <p className="mt-1 text-muted-foreground">
                    {progress && progress.total_units > 0
                      ? progress.completed_units.toLocaleString() +
                        " of " +
                        progress.total_units.toLocaleString() +
                        " learning units complete"
                      : "Distill is processing the selected source pages."}
                  </p>
                  {streamStatus === "reconnecting" ? (
                    <p className="mt-2 text-xs text-warning-text">
                      Connection lost — reconnecting to run updates…
                    </p>
                  ) : null}
                </div>
              ) : status === "failed" ? (
                <p role="alert" className="mb-4 border-b border-border pb-3 text-sm text-destructive-text">
                  {runState?.error ?? run?.error ?? "The latest Distill run failed."}
                </p>
              ) : status === "cancelled" ? (
                <p role="status" className="mb-4 border-b border-border pb-3 text-sm text-muted-foreground">
                  The latest Distill run was stopped. Start or resume it from Distill Agents.
                </p>
              ) : null}
              {guideAvailable ? (
                <p className="mb-3 text-sm text-muted-foreground">
                  Changes here apply to future runs. The published Learning Guide keeps the page selection
                  used for its run.
                </p>
              ) : (
                <p className="mb-3 text-sm text-muted-foreground">
                  Review which extracted pages Distill should use. The Learning Guide appears here after a run
                  completes; original book pages remain in the Pages tab.
                </p>
              )}
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
          )}
        </>
      )}
    </section>
  );
}
