import { readerFont } from "@/shared/markdown/reader-font";
import type { ReactNode } from "react";

import { CONTENTS_ENTRIES_PER_PAGE } from "../params";
import type { ProcessedBook } from "../entities";

type ContentsEntry = ProcessedBook["contents"][number];

/** A generated front-matter page, derived from the reconciled processed-book contents. */
export function GeneratedContentsPage({
  bookTitle,
  entries,
  pageIndex,
  pageCount,
  onOpenPage,
  toolbar,
}: {
  bookTitle: string;
  entries: readonly ContentsEntry[];
  pageIndex: number;
  pageCount: number;
  onOpenPage: (sourcePage: number) => void;
  toolbar?: ReactNode;
}) {
  const start = pageIndex * CONTENTS_ENTRIES_PER_PAGE;
  const pageEntries = entries.slice(start, start + CONTENTS_ENTRIES_PER_PAGE);

  return (
    <article
      aria-label={`Table of contents page ${pageIndex + 1} of ${pageCount}`}
      className="min-w-0 rounded-md border border-border bg-card p-4 sm:p-5"
    >
      <header className="mb-5 border-b border-border pb-4">
        <div className="flex items-center justify-between gap-2">
          <p className="mono-label">
            CONTENTS · {pageIndex + 1} OF {pageCount}
          </p>
          {toolbar}
        </div>
        <h2 className={`${readerFont.className} mt-2 text-2xl font-semibold tracking-tight`}>
          Table of Contents
        </h2>
        <p className={`${readerFont.className} mt-1 text-base text-muted-foreground`}>{bookTitle}</p>
      </header>
      <ol aria-label="Book sections" className={readerFont.className}>
        {pageEntries.map((entry) => (
          <li
            key={`${entry.source_page_number}-${entry.level}-${entry.title}`}
            className="border-b border-border last:border-b-0"
          >
            <button
              type="button"
              aria-label={`Open ${entry.title}, page ${entry.processed_page_number}`}
              onClick={() => onOpenPage(entry.source_page_number)}
              style={{ paddingInlineStart: `${Math.min(Math.max(entry.level - 1, 0) * 0.5, 2.5)}rem` }}
              className="flex min-h-9 w-full items-start justify-between gap-4 py-1.5 text-left text-sm hover:bg-muted focus-visible:outline-2 focus-visible:outline-ring"
            >
              <span className="min-w-0 break-words">{entry.title}</span>
              <span className="tabular shrink-0 text-xs text-muted-foreground">
                p. {entry.processed_page_number}
              </span>
            </button>
          </li>
        ))}
      </ol>
    </article>
  );
}
