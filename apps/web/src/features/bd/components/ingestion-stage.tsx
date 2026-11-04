"use client";

/** The Ingestion tab: upload and book selection stay in a compact toolbar above the open book. The open book is the
 * `?book=` parameter — written only by the handlers below (never by an effect), so there is nothing to chase. */

import { useRouter, useSearchParams } from "next/navigation";
import { useState } from "react";

import { useAutoChainPreference } from "../hooks/use-auto-chain-preference";
import { BOOK_PARAM, bookHref } from "../params";
import { BookView } from "./book-view";
import { LibraryPicker } from "./library-list";
import { UploadDropzone } from "./upload-dropzone";

export function IngestionStage() {
  const router = useRouter();
  const bookId = useSearchParams().get(BOOK_PARAM);
  const [refreshKey, setRefreshKey] = useState(0);
  const [autoChain, setAutoChain] = useAutoChainPreference();
  const open = (id: string | null) => router.push(bookHref(id), { scroll: false });
  const refresh = (id: string) => {
    setRefreshKey((key) => key + 1);
    open(id);
  };

  return (
    <div className="min-w-0">
      <div className="mb-5 flex flex-wrap items-center justify-between gap-2 rounded-md border border-border bg-card p-2">
        <UploadDropzone onOpen={open} autoChain={autoChain} />
        <LibraryPicker selected={bookId} onOpen={open} onRefresh={refresh} autoChain={autoChain} />
        <label className="flex min-h-9 items-center gap-2 px-2 text-xs text-muted-foreground">
          <input
            type="checkbox"
            checked={autoChain}
            onChange={(event) => setAutoChain(event.currentTarget.checked)}
            className="size-4 accent-primary"
          />
          Run Agentic AI after extraction
        </label>
      </div>
      <div className="min-w-0">
        {bookId ? (
          <BookView key={`${bookId}-${refreshKey}`} bookId={bookId} autoChain={autoChain} />
        ) : (
          <section
            aria-label="No book open"
            className="rounded-md border border-border bg-card p-6 text-sm text-muted-foreground"
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
