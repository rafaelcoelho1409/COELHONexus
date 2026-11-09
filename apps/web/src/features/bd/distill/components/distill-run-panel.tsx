"use client";

import { ArrowDownToLine } from "lucide-react";
import { apiErrorMessage } from "@/shared/lib/api-error";
import type { StreamStatus } from "@/shared/sse";
import { useRagIndex } from "../../rag/hooks/use-index-data";
import { learningGuideHtmlUrl, learningGuidePdfUrl } from "../api";
import type { DistillRunViewState } from "../domain/run-state";
import type { DistillRun } from "../entities";
import { useLearningGuide } from "../hooks/use-distill-data";
import { LearningGuideView } from "./learning-guide";

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
  const ragIndex = useRagIndex(bookId, run.run_id, state.status === "done");

  if (state.status !== "done") return null;

  return (
    <section aria-labelledby="distill-report-heading" className="mt-4 min-w-0">
      <header className="flex flex-wrap items-center justify-between gap-3 border-b border-border pb-3">
        <div className="min-w-0">
          <h3 id="distill-report-heading" className="text-base font-semibold">
            Learning Guide report
          </h3>
          <p className="mt-1 break-all font-mono text-xs text-muted-foreground">{run.run_id}</p>
        </div>
        <div className="flex flex-wrap gap-2">
          <a
            href={learningGuideHtmlUrl(bookId, run.run_id)}
            target="_blank"
            rel="noreferrer noopener"
            className="inline-flex min-h-9 items-center gap-2 rounded-sm border border-border px-3 text-sm"
          >
            <ArrowDownToLine aria-hidden className="size-4" /> Open HTML report
          </a>
          <a
            href={learningGuidePdfUrl(bookId, run.run_id)}
            className="inline-flex min-h-9 items-center gap-2 rounded-sm border border-border px-3 text-sm"
          >
            <ArrowDownToLine aria-hidden className="size-4" /> Download PDF
          </a>
        </div>
      </header>

      {streamStatus === "reconnecting" ? (
        <p role="status" className="mt-3 text-xs text-warning-text">
          Reconnecting to live run updates. The saved status remains visible.
        </p>
      ) : null}

      <section aria-label="Agentic RAG index status" className="mt-4 border border-border bg-card px-4 py-3">
        <p className="mono-label">Post-publication process</p>
        <h4 className="mt-1 text-sm font-semibold">Book retrieval index</h4>
        {ragIndex.isError ? (
          <div role="alert" className="mt-2 text-sm">
            <p className="text-warning-text">
              Index status could not be loaded — {apiErrorMessage(ragIndex.error)}
            </p>
          </div>
        ) : ragIndex.isPending ? (
          <p role="status" className="mt-2 text-sm text-muted-foreground">
            Waiting for the separate index job. It includes every Ingestion page, selected or excluded, plus
            the Learning Guide records.
          </p>
        ) : ragIndex.data?.status === "not_started" ? (
          <div className="mt-2 text-sm">
            <p className="text-muted-foreground">
              The report is published; indexing has not started. Manage this process in the Agents tab.
            </p>
          </div>
        ) : ragIndex.data?.status === "queued" || ragIndex.data?.status === "running" ? (
          <p role="status" className="mt-2 text-sm text-muted-foreground">
            {ragIndex.data.status === "queued" ? "Queued" : "Indexing"} ·{" "}
            {ragIndex.data.point_count.toLocaleString()} passages indexed so far
          </p>
        ) : ragIndex.data?.status === "done" ? (
          <p role="status" className="mt-2 text-sm text-success-text">
            Ready for later retrieval · {ragIndex.data.source_page_count.toLocaleString()} source pages ·{" "}
            {ragIndex.data.point_count.toLocaleString()} passages ·{" "}
            {ragIndex.data.graph_node_count.toLocaleString()} knowledge nodes
          </p>
        ) : (
          <div role={ragIndex.data?.status === "failed" ? "alert" : "status"} className="mt-2 text-sm">
            <p className="text-warning-text">
              {ragIndex.data?.status === "cancelled" ? "Indexing stopped" : "Indexing failed"}
              {ragIndex.data?.error ? ` — ${ragIndex.data.error}` : "."} Manage or resume this process in the
              Agents tab.
            </p>
          </div>
        )}
        <p className="mt-1 text-xs text-muted-foreground">
          Distill-linked chat and cross-book retrieval are future capabilities.
        </p>
      </section>

      {report.isPending ? (
        <p role="status" className="mt-4 text-sm text-muted-foreground">
          Opening the Learning Guide…
        </p>
      ) : report.isError ? (
        <section role="alert" className="mt-4 rounded-md border border-border bg-card p-4 text-sm">
          <p className="text-destructive-text">
            The Learning Guide could not be opened — {apiErrorMessage(report.error)}
          </p>
        </section>
      ) : (
        <div className="mt-4">
          <LearningGuideView report={report.data} quality={run.structural_quality} />
        </div>
      )}
    </section>
  );
}
