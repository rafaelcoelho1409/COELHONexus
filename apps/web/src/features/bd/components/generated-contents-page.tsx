import type { ReactNode } from "react";
import { readerFont } from "@/shared/markdown/reader-font";
import type { ProcessedBook } from "../entities";
import { CONTENTS_ENTRIES_PER_PAGE } from "../params";
import { ReaderPageFrame } from "./reader-page-frame";

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
    <ReaderPageFrame
      ariaLabel={`Table of contents page ${pageIndex + 1} of ${pageCount}`}
      label={`CONTENTS · ${pageIndex + 1} OF ${pageCount}`}
      title="Table of Contents"
      subtitle={bookTitle}
      toolbar={toolbar}
    >
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
    </ReaderPageFrame>
  );
}
