"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import type { ReactNode } from "react";
import { ContextBar } from "@/shared/layout/context-bar";
import { PAGE_PARAM, parsePageParam } from "../../params";
import { learningGuideHtmlUrl, learningGuidePdfUrl } from "../api";
import { learningGuideMarkdown, learningGuideMarkdownFilename } from "../domain/learning-guide-markdown";
import { selectDistillRun } from "../domain/run-state";
import { useDistillRuns, useLearningGuide } from "../hooks/use-distill-data";
import { DISTILL_RUN_PARAM, distillStudyHref, STUDY_VIEW_PARAM, type StudyViewId } from "../params";

const STUDY_VIEWS: readonly { id: StudyViewId; label: string }[] = [
  { id: "guide", label: "Learning Guide" },
  { id: "selection", label: "Page Selection" },
];

/** Study view navigation and report exports occupy the third Book Distiller navbar. */
export function StudyNavbar({ bookId }: { bookId: string | null }) {
  const params = useSearchParams();
  const activeView: StudyViewId = params.get(STUDY_VIEW_PARAM) === "selection" ? "selection" : "guide";
  const runId = params.get(DISTILL_RUN_PARAM);
  const page = parsePageParam(params.get(PAGE_PARAM));

  return (
    <div className="flex min-w-0 flex-wrap items-center justify-end gap-x-4 gap-y-1 max-sm:w-full">
      {bookId && activeView === "guide" ? <StudyReportActions bookId={bookId} runId={runId} /> : null}
      <nav aria-label="Study views" className="flex min-w-0 flex-wrap items-center">
        {STUDY_VIEWS.map((view) => (
          <Link
            key={view.id}
            href={distillStudyHref(bookId, runId, page, view.id)}
            aria-current={view.id === activeView ? "page" : undefined}
            className={
              view.id === activeView
                ? "inline-flex min-h-9 items-center border-b-2 border-primary px-3 text-sm font-semibold text-foreground"
                : "inline-flex min-h-9 items-center border-b-2 border-transparent px-3 text-sm font-medium text-muted-foreground hover:text-foreground"
            }
          >
            {view.label}
          </Link>
        ))}
      </nav>
    </div>
  );
}

function StudyReportActions({ bookId, runId }: { bookId: string; runId: string | null }) {
  const runs = useDistillRuns(bookId);
  const selected = selectDistillRun(runs.data ?? [], runId);
  const ready = selected?.status === "done";
  const report = useLearningGuide(bookId, selected?.run_id ?? "", ready);

  if (!ready || !selected) return null;

  return (
    <fieldset className="m-0 inline-flex overflow-hidden rounded-sm border border-border bg-background p-0">
      <legend className="sr-only">Learning Guide exports</legend>
      <button
        type="button"
        disabled={!report.data}
        title={report.data ? "Download Markdown report" : "Preparing Markdown report"}
        onClick={() => {
          const guide = report.data;
          if (!guide) return;
          const blob = new Blob([learningGuideMarkdown(guide)], { type: "text/markdown;charset=utf-8" });
          const objectUrl = URL.createObjectURL(blob);
          const link = document.createElement("a");
          link.href = objectUrl;
          link.download = learningGuideMarkdownFilename(guide);
          link.click();
          window.setTimeout(() => URL.revokeObjectURL(objectUrl), 0);
        }}
        className="inline-flex min-h-9 items-center border-r border-border px-3 text-sm font-medium hover:bg-muted disabled:cursor-wait disabled:text-muted-foreground"
      >
        Markdown
      </button>
      <a
        href={learningGuideHtmlUrl(bookId, selected.run_id)}
        target="_blank"
        rel="noreferrer noopener"
        className="inline-flex min-h-9 items-center border-r border-border px-3 text-sm font-medium hover:bg-muted"
      >
        HTML
      </a>
      <a
        href={learningGuidePdfUrl(bookId, selected.run_id)}
        download
        className="inline-flex min-h-9 items-center px-3 text-sm font-medium hover:bg-muted"
      >
        PDF
      </a>
    </fieldset>
  );
}

/** The bar is rendered for Study at the same screen-wide slot as the other Book Distiller views. */
export function StudyContextBar({ bookId, start }: { bookId: string | null; start: ReactNode }) {
  return <ContextBar pinned start={start} end={<StudyNavbar bookId={bookId} />} />;
}
