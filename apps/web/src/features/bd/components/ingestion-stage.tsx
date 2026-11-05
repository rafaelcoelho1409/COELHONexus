"use client";

/** The Ingestion tab. The page's third navbar holds the library picker, the upload control and the auto-chain choice on
 * the left and the open book's Pipeline | Materials views on the right. The open book is the `?book=` parameter —
 * written only by the handlers below (never by an effect), so there is nothing to chase. */

import { useRouter, useSearchParams } from "next/navigation";
import { useState } from "react";

import { ContextBar } from "@/shared/layout/context-bar";

import { useAutoChainPreference } from "../hooks/use-auto-chain-preference";
import { BOOK_PARAM, type BookViewId, bookHref } from "../params";
import { BookView } from "./book-view";
import { BookViewTabs } from "./book-view-tabs";
import { LibraryPicker } from "./library-list";
import { UploadDropzone } from "./upload-dropzone";

export function IngestionStage() {
  const router = useRouter();
  const bookId = useSearchParams().get(BOOK_PARAM);
  const [refreshKey, setRefreshKey] = useState(0);
  const [autoChain, setAutoChain] = useAutoChainPreference();
  // The chosen view belongs to one opening of one book: another book, or the same one extracted again, starts on
  // Pipeline. Deriving that from the key keeps it out of an effect.
  const openingKey = `${bookId}-${refreshKey}`;
  const [chosen, setChosen] = useState<{ openingKey: string; view: BookViewId } | null>(null);
  const view = chosen?.openingKey === openingKey ? chosen.view : "pipeline";
  const open = (id: string | null) => router.push(bookHref(id), { scroll: false });
  const refresh = (id: string) => {
    setRefreshKey((key) => key + 1);
    open(id);
  };

  return (
    <div className="min-w-0">
      <ContextBar
        start={
          <>
            <LibraryPicker selected={bookId} onOpen={open} onRefresh={refresh} autoChain={autoChain} />
            <UploadDropzone onOpen={open} autoChain={autoChain} />
            <label className="flex min-h-9 items-center gap-2 text-xs text-muted-foreground">
              <input
                type="checkbox"
                checked={autoChain}
                onChange={(event) => setAutoChain(event.currentTarget.checked)}
                className="size-4 accent-primary"
              />
              Run Agentic AI after extraction
            </label>
          </>
        }
        end={
          bookId ? (
            <BookViewTabs view={view} onChange={(next) => setChosen({ openingKey, view: next })} />
          ) : undefined
        }
      />
      <div className="min-w-0">
        {bookId ? (
          <BookView
            key={openingKey}
            bookId={bookId}
            autoChain={autoChain}
            activeView={view}
            onViewChange={(next) => setChosen({ openingKey, view: next })}
          />
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
