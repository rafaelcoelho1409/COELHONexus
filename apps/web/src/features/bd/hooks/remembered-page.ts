import { parseRememberedPage } from "../domain/reader-layout";
import { LAST_PAGE_STORAGE_PREFIX } from "../params";

/** The page this browser left the book on, or null (nothing stored, storage blocked, or a page the book does not have). */
export function readRememberedPage(bookId: string, pageCount: number): number | null {
  try {
    return parseRememberedPage(
      window.localStorage.getItem(`${LAST_PAGE_STORAGE_PREFIX}${bookId}`),
      pageCount,
    );
  } catch {
    return null;
  }
}

/** Remember the page the reader chose, so reopening the book continues there. A blocked storage just forgets it. */
export function rememberPage(bookId: string, page: number): void {
  try {
    window.localStorage.setItem(`${LAST_PAGE_STORAGE_PREFIX}${bookId}`, String(page));
  } catch {
    // not remembered: the book opens on its first page
  }
}
