"use client";

/** One chapter on the Study tab: its title and facts, then the text (or why there is none). The page frames it as a card
 * between the side columns; the reading window shows it bare, on the whole screen. */

import type { ReactNode } from "react";

import { Badge } from "@/shared/ui/badge";

import { chapterFacts, humanizeChapterId } from "../domain/chapters";
import type { SynthChapters } from "../entities";

export function StudyArticle({
  chapter,
  hasChapters,
  framed,
  toolbar,
  children,
}: {
  /** The chapter on show; null when there is none to pick. */
  chapter: SynthChapters["chapters"][number] | null;
  /** Whether the plan has any chapter at all (tells "pick one" from "nothing yet"). */
  hasChapters: boolean;
  framed: boolean;
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
                <h2 data-testid="study-title" className="text-2xl font-semibold tracking-tight">
                  {chapter.title || humanizeChapterId(chapter.id)}
                </h2>
                {chapter.rendered ? (
                  <p className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted-foreground">
                    {chapter.audit_passed ? (
                      <Badge className="bg-success/10 text-success-text">Audit passed</Badge>
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
          {children}
        </>
      ) : hasChapters ? (
        <p className="text-sm text-muted-foreground">Pick a chapter from the list.</p>
      ) : (
        <p className="text-sm text-muted-foreground">The plan is pending — nothing to study yet.</p>
      )}
    </section>
  );
}
