import { BOOK_PARAM, bookPagesHref } from "../params";

export const DISTILL_RUN_PARAM = "run";
export const DISTILL_REFRESH_MS = 4_000;

/** A page citation opens the shared Pages stage; run navigation stays on Distill. */
export function distillHref(bookId?: string | null, runId?: string | null, page?: number | null): string {
  if (page !== undefined && page !== null) return bookPagesHref(bookId, page);
  const params = new URLSearchParams();
  if (bookId) params.set(BOOK_PARAM, bookId);
  if (runId) params.set(DISTILL_RUN_PARAM, runId);
  const query = params.toString();
  return query ? `/bd/distill?${query}` : "/bd/distill";
}
