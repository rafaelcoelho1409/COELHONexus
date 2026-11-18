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
  const completedBatches = index?.graph_batches_completed ?? 0;
  const totalBatches = index?.graph_batches_total ?? 0;
  const graphNodeDone = index?.node_status.neo4j === "done";
  const graphProgress = graphNodeDone
    ? 1
    : totalBatches > 0
      ? Math.min(1, completedBatches / totalBatches)
      : 0;
  const completedWithoutGraph = completedNodes - (graphNodeDone ? 1 : 0);
  return Math.min(
    100,
    Math.max(0, Math.round(((completedWithoutGraph + graphProgress) / Math.max(1, totalNodes)) * 100)),
  );
}

export function retrievalIndexStatusLabel(
  index: RetrievalIndexStatus | null,
  run: AgenticRunStatus | null,
): string {
  if (!run) return "Waiting for Ingestion";
  if (!index) return "Not started";
  if (index.status === "done") return retrievalIndexNeedsUpdate(index, run) ? "Update available" : "Complete";
  if (index.status === "running") return "Processing";
  if (index.status === "queued") return "Queued";
  if (index.status === "failed") return "Failed";
  return "Stopped";
}
