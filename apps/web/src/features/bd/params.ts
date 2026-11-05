import type { Section } from "@/shared/layout/section";

/** Book Distiller stages. Only Ingestion exists; Distill and Research join this list when they do (the tab strip
 * and the route table are built from it). */
const BD_STAGES = [{ key: "ingestion", label: "Ingestion", href: "/bd/ingestion" }] as const;

/** The title and stage tabs in the second navbar. `/bd` opens the Ingestion stage, so it is the current tab there. */
export const BD_SECTION: Section = {
  href: "/bd",
  title: "Book Distiller",
  description: "Upload a book, watch it being read, study what was extracted",
  defaultTab: "ingestion",
  tabs: BD_STAGES.map((stage) => ({ ...stage, tab: true, carry: false })),
};

/** The server refuses a larger upload (413); the dropzone says so before sending 300 MB for nothing. */
export const MAX_UPLOAD_BYTES = 300 * 1024 * 1024;
export const MAX_UPLOAD_LABEL = "300 MB";

/** The two views of an open book, drawn as links in the page's third navbar. */
export const BOOK_VIEWS = [
  ["pipeline", "Agents"],
  ["materials", "Pages"],
] as const;
export type BookViewId = (typeof BOOK_VIEWS)[number][0];

/** The address parameter that names the open book. */
export const BOOK_PARAM = "book";
export const AUTO_CHAIN_STORAGE_KEY = "bd-ingestion-auto-chain";

/** The reader's layout (which side columns are open, how wide the text runs) and the page each book was left on are
 * per-browser preferences. */
export const READER_LAYOUT_STORAGE_KEY = "bd:reader:layout";
export const LAST_PAGE_STORAGE_PREFIX = "bd:reader:page:";

/** While any book is being extracted the library list is re-read this often (the open book's stream is live anyway). */
export const LIBRARY_REFRESH_MS = 4_000;

/** The open book's snapshot is re-read this often while a run is live — a fallback for a stream that cannot connect. */
export const SNAPSHOT_REFRESH_MS = 6_000;

/** Search fetches content in small batches so a large book does not flood the API. */
export const BOOK_SEARCH_BATCH_SIZE = 8;
export const BOOK_SEARCH_MAX_HITS = 30;
export const BOOK_SEARCH_STALE_MS = 60_000;

/** A cancel lands between 5-page slices; measured on a real 314-page book, that took 15–95 s (a slice of
 * table-of-contents pages took 96 s). The UI stops saying "Cancelling…" after this long even if the terminal event
 * has not arrived; the stream stays connected, so the event is still applied when it comes. */
export const CANCEL_SAFETY_MS = 150_000;

/** An in-page notice hides itself after this long. */
export const NOTICE_MS = 8_000;

/** The ingestion page of one book, or of none. */
export function bookHref(bookId?: string | null): string {
  const base = "/bd/ingestion";
  return bookId ? `${base}?${BOOK_PARAM}=${encodeURIComponent(bookId)}` : base;
}
