"use client";

/** The Ingestion and Pages stages share the library and book reader. The open book is the `?book=` parameter —
 * written only by the handlers below (never by an effect), so there is nothing to chase. */

import { useRouter, useSearchParams } from "next/navigation";
import { useState } from "react";

import { ContextBar } from "@/shared/layout/context-bar";

import { useAutoChainPreference } from "../hooks/use-auto-chain-preference";
import { useLibrary } from "../hooks/use-bd-data";
import { BOOK_PARAM, type BookViewId, bookHref, bookPagesHref, PAGE_PARAM, parsePageParam } from "../params";
import { BookView } from "./book-view";
import { LibraryPicker } from "./library-list";
import { UploadDropzone } from "./upload-dropzone";

export function IngestionStage({ view = "pipeline" }: { view?: BookViewId }) {
  const router = useRouter();
  const searchParams = useSearchParams();
  const bookId = searchParams.get(BOOK_PARAM);
  const initialPage = parsePageParam(searchParams.get(PAGE_PARAM));
  const [refreshKey, setRefreshKey] = useState(0);
  const [autoChain, setAutoChain] = useAutoChainPreference();
  const library = useLibrary();
  const selectedBook = library.data?.find((book) => book.book_id === bookId);
  const selectedTitle = selectedBook?.title.trim() || selectedBook?.filename;
  const openingKey = `${bookId}-${initialPage ?? ""}-${refreshKey}`;
  const open = (id: string | null) =>
    router.push(view === "materials" ? bookPagesHref(id) : bookHref(id), { scroll: false });
  const refresh = (id: string) => {
    setRefreshKey((key) => key + 1);
    open(id);
  };

  return (
    <div className="min-w-0">
      <ContextBar
        start={
          <>
            <LibraryPicker
              selected={bookId}
              onOpen={open}
              onRefresh={refresh}
              autoChain={autoChain}
              showSelectedTitle={view !== "materials"}
            />
            {view !== "materials" ? (
              <>
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
            ) : null}
          </>
        }
        end={
          view === "materials" && selectedTitle ? (
            <p
              className="ml-auto min-w-0 max-w-[min(48vw,40rem)] break-words text-right text-base font-semibold text-foreground max-sm:w-full max-sm:max-w-full"
              title={selectedTitle}
            >
              {selectedTitle}
            </p>
          ) : undefined
        }
      />
      <div className="min-w-0">
        {bookId ? (
          <BookView
            key={openingKey}
            bookId={bookId}
            initialPage={initialPage}
            autoChain={autoChain}
            activeView={view}
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
