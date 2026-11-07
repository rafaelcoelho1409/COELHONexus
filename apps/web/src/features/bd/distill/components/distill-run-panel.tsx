"use client";

import { ArrowDownToLine } from "lucide-react";

import { apiErrorMessage } from "@/shared/lib/api-error";
import { learningGuideHtmlUrl, learningGuidePdfUrl } from "../api";
import type { DistillRun } from "../entities";
import { useDistillRunStream, useLearningGuide } from "../hooks/use-distill-data";
import { LearningGuideView } from "./learning-guide";

export function DistillRunPanel({ bookId, run }: { bookId: string; run: DistillRun }) {
  const live = useDistillRunStream(bookId, run);
  const report = useLearningGuide(bookId, run.run_id, run.status === "done");
  const totalUnits = live.state.progress.total_units;
  const completedUnits = live.state.progress.completed_units;
  const totalChapters = live.state.progress.total_chapters;
  const completedChapters = live.state.progress.completed_chapters;

  return (
    <section aria-labelledby="distill-run-status" className="min-w-0">
      <div className="rounded-md border border-border bg-card p-4">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="min-w-0">
            <h3 id="distill-run-status" className="text-base font-semibold">
              Run status
            </h3>
            <p className="mt-1 text-sm" aria-live="polite">
              {statusLabel(live.state.status)}
            </p>
            <p className="mt-1 break-all text-xs text-muted-foreground">Run {run.run_id}</p>
          </div>
          {run.status === "done" ? (
            <div className="flex flex-wrap gap-2">
              <a
                href={learningGuideHtmlUrl(bookId, run.run_id)}
                target="_blank"
                rel="noreferrer noopener"
                className="inline-flex min-h-9 items-center gap-2 rounded-sm border border-border px-3 text-sm"
              >
                <ArrowDownToLine aria-hidden className="size-4" /> HTML
              </a>
              <a
                href={learningGuidePdfUrl(bookId, run.run_id)}
                className="inline-flex min-h-9 items-center gap-2 rounded-sm border border-border px-3 text-sm"
              >
                <ArrowDownToLine aria-hidden className="size-4" /> PDF
              </a>
            </div>
          ) : null}
        </div>

        {live.state.status === "running" || live.state.status === "queued" ? (
          <div className="mt-4 space-y-2">
            <p className="text-sm text-muted-foreground">
              {live.state.status === "queued"
                ? "Waiting for a worker."
                : `Current stage: ${live.state.progress.current_node ?? "Preparing"}`}
            </p>
            <p className="text-xs text-muted-foreground" aria-live="polite">
              Learning units {completedUnits}/{totalUnits} · chapters {completedChapters}/{totalChapters}
            </p>
            {live.status === "reconnecting" ? (
              <p className="text-xs text-warning-text">Reconnecting to run updates…</p>
            ) : null}
          </div>
        ) : null}

        {live.state.status === "failed" ? (
          <p role="alert" className="mt-3 text-sm text-destructive-text">
            {live.state.error ?? run.error ?? "Distill could not complete."}
          </p>
        ) : null}

        {run.structural_quality ? (
          <div className="mt-4 grid grid-cols-1 gap-3 border-t border-border pt-3 sm:grid-cols-3">
            <QualityValue
              label="Page coverage"
              value={`${Math.round(run.structural_quality.page_coverage_rate * 100)}%`}
            />
            <QualityValue
              label="Valid source references"
              value={`${run.structural_quality.valid_source_reference_count}/${run.structural_quality.source_reference_count}`}
            />
            <QualityValue
              label="Evidence checks"
              value={`${run.structural_quality.supported_evidence_quote_count}/${run.structural_quality.evidence_quote_count}`}
            />
          </div>
        ) : null}
      </div>

      {run.status === "done" ? (
        report.isPending ? (
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
            <LearningGuideView report={report.data} />
          </div>
        )
      ) : null}
    </section>
  );
}

function QualityValue({ label, value }: { label: string; value: string }) {
  return (
    <div className="min-w-0">
      <p className="text-xs text-muted-foreground">{label}</p>
      <p className="mt-1 break-words text-sm tabular">{value}</p>
    </div>
  );
}

function statusLabel(status: DistillRun["status"]): string {
  if (status === "queued") return "Queued";
  if (status === "running") return "Running";
  if (status === "done") return "Complete";
  if (status === "failed") return "Failed";
  return "Cancelled";
}
