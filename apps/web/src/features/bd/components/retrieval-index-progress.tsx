"use client";

import { Check } from "lucide-react";
import { apiErrorMessage } from "@/shared/lib/api-error";
import { retrievalIndexNeedsUpdate, retrievalIndexProgress, retrievalIndexStatusLabel } from "../domain/retrieval-index";
import type { AgenticRunStatus, RetrievalIndexStatus } from "../entities";
import { useBdOverlays } from "../hooks/use-overlays";
import {
  useCancelRetrievalIndex,
  useResumeRetrievalIndex,
  useStartRetrievalIndex,
  useWipeRetrievalIndex,
} from "../hooks/use-bd-data";
import { ActionButton } from "./action-button";

export function RetrievalIndexProgress({
  bookId,
  run,
  index,
}: {
  bookId: string;
  run: AgenticRunStatus | null;
  index: RetrievalIndexStatus | null;
}) {
  const start = useStartRetrievalIndex();
  const resume = useResumeRetrievalIndex();
  const cancel = useCancelRetrievalIndex();
  const wipe = useWipeRetrievalIndex();
  const { confirm, notice } = useBdOverlays();
  const agenticRunId = run?.agentic_run_id ?? "";
  const live = index?.status === "queued" || index?.status === "running";
  const busy = start.isPending || resume.isPending || cancel.isPending || wipe.isPending;
  const outdated = retrievalIndexNeedsUpdate(index, run);
  const canStart = run !== null && (!index || outdated);
  const canResume = !outdated && (index?.status === "failed" || index?.status === "cancelled");
  const completedNodes = Object.values(index?.node_status ?? {}).filter((status) => status === "done").length;
  const totalNodes = Math.max(5, Object.keys(index?.node_status ?? {}).length);
  const progress = retrievalIndexProgress(index, completedNodes, totalNodes);
  const statusText = retrievalIndexStatusLabel(index, run);
  const successful = index?.status === "done" && !outdated;
  const mutationError = start.error ?? resume.error ?? cancel.error ?? wipe.error;
  const error = mutationError ? apiErrorMessage(mutationError) : index?.error;

  const startIndex = () => {
    if (!run || !canStart || busy || live) return;
    start.mutate(
      { bookId, agenticRunId },
      {
        onSuccess: () => notice("Agentic RAG queued."),
        onError: (cause) => notice(apiErrorMessage(cause), "error"),
      },
    );
  };
  const resumeIndex = () => {
    if (!run || !canResume || busy) return;
    resume.mutate(
      { bookId, agenticRunId },
      {
        onSuccess: () => notice("Agentic RAG queued to resume."),
        onError: (cause) => notice(apiErrorMessage(cause), "error"),
      },
    );
  };
  const stopIndex = () => {
    if (!run || !live || busy) return;
    cancel.mutate(
      { bookId, agenticRunId },
      {
        onSuccess: (result) =>
          notice(result.status === "cancelled" ? "Agentic RAG stopped." : "Agentic RAG stop requested."),
        onError: (cause) => notice(apiErrorMessage(cause), "error"),
      },
    );
  };
  const wipeIndex = async () => {
    if (!run || !index || live || busy) return;
    const confirmed = await confirm({
      title: "Wipe Agentic RAG index?",
      message:
        "Deletes this run’s retrieval index from Elasticsearch, Qdrant, and Neo4j, along with its index artifacts and checkpoints. The processed pages and uploaded book are preserved.",
      confirmLabel: "Wipe",
    });
    if (!confirmed) return;
    wipe.mutate(
      { bookId, agenticRunId },
      {
        onSuccess: () => notice("Agentic RAG index wiped. Ingestion pages are unchanged."),
        onError: (cause) => notice(apiErrorMessage(cause), "error"),
      },
    );
  };

  return (
    <section
      aria-label="Agentic RAG progress"
      className="min-w-0 rounded-md border border-border bg-card p-3"
    >
      <div className="flex min-h-9 flex-wrap items-center gap-2">
        <h3 className="flex items-center gap-1.5 text-sm font-medium">
          Agentic RAG
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
          {index ? ` · ${completedNodes} / ${totalNodes} stages` : ""}
        </span>
        <div className="ml-auto flex flex-wrap items-center justify-end gap-1.5">
          <ActionButton
            onClick={canResume ? resumeIndex : startIndex}
            disabled={busy || live || (!canResume && !canStart)}
            aria-busy={start.isPending || resume.isPending}
            aria-label={canResume ? "Resume Agentic RAG" : "Start Agentic RAG"}
            title={
              !canResume && !canStart && index?.status === "done"
                ? "This retrieval index is current. Wipe it to build it again."
                : undefined
            }
          >
            {start.isPending
              ? "Starting…"
              : resume.isPending
                ? "Resuming…"
                : canResume
                  ? "Resume"
                  : "Start"}
          </ActionButton>
          <ActionButton
            onClick={stopIndex}
            disabled={!live || busy}
            aria-busy={cancel.isPending}
            aria-label="Stop Agentic RAG"
          >
            {cancel.isPending ? "Stopping…" : "Stop"}
          </ActionButton>
          <ActionButton
            onClick={() => void wipeIndex()}
            disabled={!run || !index || live || busy}
            aria-busy={wipe.isPending}
            aria-label="Wipe Agentic RAG"
          >
            {wipe.isPending ? "Wiping…" : "Wipe"}
          </ActionButton>
        </div>
      </div>
      <div
        role="progressbar"
        aria-label="Agentic RAG progress"
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={progress}
        aria-valuetext={`${progress}% · ${completedNodes} of ${totalNodes} stages complete`}
        className="mt-2 h-1.5 w-full overflow-hidden rounded-sm bg-muted"
      >
        <div className="h-full bg-primary" style={{ width: `${progress}%` }} />
      </div>
      {error ? (
        <p role="alert" className="mt-2 break-words text-xs text-destructive-text">
          Agentic RAG failed — {error}
        </p>
      ) : null}
    </section>
  );
}
