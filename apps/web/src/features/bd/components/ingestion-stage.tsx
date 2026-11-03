"use client";

/** The Ingestion tab: the upload and the library on the left, the open book on the right. The open book is the
 * `?book=` parameter — written only by the handlers below (never by an effect), so there is nothing to chase. */

import { useRouter, useSearchParams } from "next/navigation";

import { BOOK_PARAM, bookHref } from "../params";
import { BookView } from "./book-view";
import { LibraryList } from "./library-list";
import { UploadDropzone } from "./upload-dropzone";

export function IngestionStage() {
  const router = useRouter();
  const bookId = useSearchParams().get(BOOK_PARAM);
  const open = (id: string | null) => router.push(bookHref(id), { scroll: false });

  return (
    <div className="grid grid-cols-1 gap-6 lg:grid-cols-[19rem_minmax(0,1fr)]">
      <div className="flex min-w-0 flex-col gap-5">
        <UploadDropzone onOpen={open} />
        <LibraryList selected={bookId} onOpen={open} />
      </div>
      <div className="min-w-0">
        {bookId ? (
          <BookView key={bookId} bookId={bookId} />
        ) : (
          <section
            aria-label="No book open"
            className="rounded-md border border-border bg-card p-6 text-sm text-muted-foreground"
          >
            <p className="font-medium text-foreground">No book open.</p>
            <p className="mt-1">
              Upload a PDF, or open one from the library. The extraction is shown page by page while it is
              read.
            </p>
          </section>
        )}
      </div>
    </div>
  );
}
