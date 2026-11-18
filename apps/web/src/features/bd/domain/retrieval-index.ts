import { RETRIEVAL_INDEX_WORKFLOW_VERSION, type AgenticRunStatus, type RetrievalIndexStatus } from "../entities";

export function retrievalIndexNeedsUpdate(index: RetrievalIndexStatus | null, run: AgenticRunStatus | null): boolean {
  return Boolean(
    index &&
      (index.workflow_version !== RETRIEVAL_INDEX_WORKFLOW_VERSION ||
        (run?.processed_book_revision !== null &&
          run?.processed_book_revision !== undefined &&
          index.processed_book_revision !== run.processed_book_revision)),
  );
}

export function retrievalIndexProgress(
  index: RetrievalIndexStatus | null,
  completedNodes: number,
  totalNodes: number,
): number {
  if (index?.status === "done") return 100;
  return Math.min(
    100,
    Math.max(0, Math.round((completedNodes / Math.max(1, totalNodes)) * 100)),
  );
}

export function retrievalIndexStatusLabel(
  index: RetrievalIndexStatus | null,
  run: AgenticRunStatus | null,
): string {
  if (!run) return "Waiting for Ingestion";
  if (!index) return "Not started";
  if (index.status === "done") return retrievalIndexNeedsUpdate(index, run) ? "Update available" : "Complete";
  if (index.cancel_requested) return "Stopping";
  if (index.status === "running") return "Processing";
  if (index.status === "queued") return "Queued";
  if (index.status === "failed") return "Failed";
  return "Stopped";
}
