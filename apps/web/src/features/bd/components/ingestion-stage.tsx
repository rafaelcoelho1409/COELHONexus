"use client";

/** The Ingestion and Pages stages share the library and book reader. The open book is the `?book=` parameter —
 * written only by the handlers below (never by an effect), so there is nothing to chase. */

import { useRouter, useSearchParams } from "next/navigation";
import { useState } from "react";

import { ContextBar } from "@/shared/layout/context-bar";

import { BOOK_PARAM, type BookViewId, bookHref, bookPagesHref, PAGE_PARAM, parsePageParam } from "../params";
import { BookView } from "./book-view";
import { LibraryPicker } from "./library-list";
import { UploadDropzone } from "./upload-dropzone";

export function IngestionStage({ view = "pipeline" }: { view?: BookViewId }) {
  const router = useRouter();
  const searchParams = useSearchParams();
  const bookId = searchParams.get(BOOK_PARAM);
  const initialPage = parsePageParam(searchParams.get(PAGE_PARAM));
  const [pageControlsHost, setPageControlsHost] = useState<HTMLDivElement | null>(null);
  const [uploadProgressHost, setUploadProgressHost] = useState<HTMLDivElement | null>(null);
  const autoChain = false;
  const openingKey = `${view}-${bookId}-${initialPage ?? ""}`;
  const open = (id: string | null) =>
    router.push(view === "materials" ? bookPagesHref(id) : bookHref(id), { scroll: false });
  return (
    <div className={view === "materials" ? "flex min-h-0 min-w-0 flex-1 flex-col" : "min-w-0"}>
      <ContextBar
        pinned
        start={
          <>
            <LibraryPicker selected={bookId} onOpen={open} />
            {view === "materials" ? (
              <div ref={setPageControlsHost} className="flex min-w-0 flex-wrap items-center gap-1.5" />
            ) : null}
            {view !== "materials" ? (
              <UploadDropzone onOpen={open} autoChain={autoChain} progressHost={uploadProgressHost} />
            ) : null}
          </>
        }
      />
      <div className={view === "materials" ? "flex min-h-0 min-w-0 flex-1 flex-col" : "min-w-0"}>
        {view !== "materials" ? <div ref={setUploadProgressHost} className="min-w-0" /> : null}
        {bookId ? (
          <BookView
            key={openingKey}
            bookId={bookId}
            initialPage={initialPage}
            autoChain={autoChain}
            activeView={view}
            pageControlsHost={view === "materials" ? pageControlsHost : null}
          />
        ) : (
          <section
            aria-label="No book open"
            className={
              view === "materials"
                ? "flex min-h-0 flex-1 items-center justify-center rounded-md border border-border bg-card p-6 text-center text-sm text-muted-foreground"
                : "rounded-md border border-border bg-card p-6 text-sm text-muted-foreground"
            }
          >
            <p className="font-medium text-foreground">No book open.</p>
            <p className="mt-1">
              Upload a PDF or EPUB, or open one from the library. Extracted pages and sections appear here as
              the book is read.
            </p>
          </section>
        )}
      </div>
    </div>
  );
}
