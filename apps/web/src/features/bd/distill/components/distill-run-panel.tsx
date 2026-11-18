"use client";

import { ArrowDownToLine } from "lucide-react";
import { apiErrorMessage } from "@/shared/lib/api-error";
import type { StreamStatus } from "@/shared/sse";
import { learningGuideHtmlUrl, learningGuidePdfUrl } from "../api";
import { learningGuideMarkdown, learningGuideMarkdownFilename } from "../domain/learning-guide-markdown";
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

  function downloadMarkdown() {
    if (!report.data) return;
    const blob = new Blob([learningGuideMarkdown(report.data)], { type: "text/markdown;charset=utf-8" });
    const objectUrl = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = objectUrl;
    link.download = learningGuideMarkdownFilename(report.data);
    link.click();
    window.setTimeout(() => URL.revokeObjectURL(objectUrl), 0);
  }

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
          {report.data ? (
            <button
              type="button"
              onClick={downloadMarkdown}
              className="inline-flex min-h-9 items-center gap-2 rounded-sm border border-border px-3 text-sm"
            >
              <ArrowDownToLine aria-hidden className="size-4" /> Download Markdown
            </button>
          ) : null}
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
          <LearningGuideReader report={report.data} quality={run.structural_quality} />
        </div>
      )}
    </section>
  );
}
