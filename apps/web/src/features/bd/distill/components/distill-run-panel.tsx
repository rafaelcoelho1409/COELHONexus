"use client";

import { apiErrorMessage } from "@/shared/lib/api-error";
import type { StreamStatus } from "@/shared/sse";
import type { DistillRunViewState } from "../domain/run-state";
import type { DistillRun } from "../entities";
import { useLearningGuide } from "../hooks/use-distill-data";
import { LearningGuideReader } from "./learning-guide-reader";

export function DistillRunPanel({
  bookId,
  run,
  state,
  streamStatus,
}: {
  bookId: string;
  run: DistillRun;
  state: DistillRunViewState;
  streamStatus: StreamStatus;
}) {
  const report = useLearningGuide(bookId, run.run_id, state.status === "done");

  if (state.status !== "done") return null;

  return (
    <section aria-label="Learning Guide" className="flex min-h-0 min-w-0 flex-1 flex-col">
      {streamStatus === "reconnecting" ? (
        <p role="status" className="shrink-0 py-2 text-xs text-warning-text">
          Reconnecting to live run updates. The saved status remains visible.
        </p>
      ) : null}

      {report.isPending ? (
        <p
          role="status"
          className="flex min-h-0 flex-1 items-center justify-center text-sm text-muted-foreground"
        >
          Opening the Learning Guide…
        </p>
      ) : report.isError ? (
        <section
          role="alert"
          className="flex min-h-0 flex-1 flex-col items-center justify-center p-4 text-sm"
        >
          <p className="text-destructive-text">
            The Learning Guide could not be opened — {apiErrorMessage(report.error)}
          </p>
        </section>
      ) : (
        <div className="flex min-h-0 min-w-0 flex-1 flex-col">
          <LearningGuideReader report={report.data} quality={run.structural_quality} />
        </div>
      )}
    </section>
  );
}
