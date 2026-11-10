"use client";

/** One chapter on the Study tab: its title and facts, then the text (or why there is none). The page frames it as a card
 * between the side columns; the reading window shows it bare, on the whole screen. */

import type { ReactNode } from "react";

import { ddStudyFont } from "@/shared/markdown/dd-study-font";
import { Badge } from "@/shared/ui/badge";

import { chapterFacts, humanizeChapterId } from "../domain/chapters";
import type { SynthChapters } from "../entities";
import { StudyActionButton } from "./study-action-button";

export type StudyContentView = "rendered" | "source";

export function StudyArticle({
  chapter,
  hasChapters,
  framed,
  position,
  contentView,
  onContentView,
  canChooseContentView,
  sourceFiles,
  onOpenSource,
  toolbar,
  children,
}: {
  /** The chapter on show; null when there is none to pick. */
  chapter: SynthChapters["chapters"][number] | null;
  /** Whether the plan has any chapter at all (tells "pick one" from "nothing yet"). */
  hasChapters: boolean;
  framed: boolean;
  /** The chapter's position in the planned study. */
  position: string;
  contentView: StudyContentView;
  onContentView: (view: StudyContentView) => void;
  canChooseContentView: boolean;
  sourceFiles: string[];
  onOpenSource: (basename: string) => void;
  /** Buttons on the right of the title. */
  toolbar?: ReactNode;
  /** The chapter's text, or the loading and error lines. */
  children: ReactNode;
}) {
  return (
    <section
      aria-label="Chapter"
      className={`min-w-0 ${framed ? "rounded-md border border-border bg-card p-5" : ""}`}
    >
      {chapter ? (
        <>
          <header className="mb-5 border-b border-border pb-4">
            <div className="flex items-start justify-between gap-3">
              <div className="min-w-0">
                <p className="mb-1 font-mono text-xs uppercase tracking-wide text-muted-foreground">
                  Chapter {position}
                </p>
                <h2 data-testid="study-title" className="text-2xl font-semibold tracking-tight">
                  {chapter.title || humanizeChapterId(chapter.id)}
                </h2>
                {chapter.rendered ? (
                  <p className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted-foreground">
                    {chapter.audit_passed ? (
                      <Badge className="bg-success/10 text-success-text">Audit passed</Badge>
                    ) : chapter.needs_review || (chapter.rendered && chapter.audit_passed === false) ? (
                      <Badge variant="outline">Needs review</Badge>
                    ) : null}
                    {chapterFacts(chapter).map((fact) => (
                      <span key={fact}>{fact}</span>
                    ))}
                  </p>
                ) : null}
              </div>
              {toolbar}
            </div>
          </header>
          {canChooseContentView ? (
            <div className="mb-4 flex flex-wrap items-center justify-between gap-2">
              <fieldset className="m-0 inline-flex overflow-hidden rounded-sm border border-border bg-background p-0">
                <legend className="sr-only">Content view</legend>
                {(
                  [
                    ["rendered", "Rendered"],
                    ["source", "Markdown source"],
                  ] as const
                ).map(([value, label]) => (
                  <StudyActionButton
                    key={value}
                    aria-pressed={contentView === value}
                    onClick={() => onContentView(value)}
                    className="min-h-9 rounded-none border-0 border-r border-r-border px-2.5 last:border-r-0"
                  >
                    {label}
                  </StudyActionButton>
                ))}
              </fieldset>
            </div>
          ) : null}
          {children}
          {sourceFiles.length > 0 ? (
            <details className="mt-5 rounded-sm border border-border p-3">
              <summary className="flex min-h-9 cursor-pointer items-center text-sm font-medium">
                Source files · {sourceFiles.length}
              </summary>
              <ul aria-label="Chapter source files" className="mt-2 flex flex-wrap gap-2">
                {sourceFiles.map((basename) => (
                  <li key={basename}>
                    <StudyActionButton onClick={() => onOpenSource(basename)}>{basename}</StudyActionButton>
                  </li>
                ))}
              </ul>
            </details>
          ) : null}
        </>
      ) : hasChapters ? (
        <p className="text-sm text-muted-foreground">Pick a chapter from the list.</p>
      ) : (
        <p className="text-sm text-muted-foreground">The plan is pending — nothing to study yet.</p>
      )}
    </section>
  );
}

/** DD uses a technical reading face while keeping BD's Literata treatment separate. */
export function StudyRenderedChapter({ children }: { children: ReactNode }) {
  return <div className={`${ddStudyFont.className} reader-prose`}>{children}</div>;
}
