import { BOOK_PARAM, PAGE_PARAM } from "../params";

export const DISTILL_RUN_PARAM = "run";
export const DISTILL_REFRESH_MS = 4_000;
export const STUDY_LAST_SECTION_STORAGE_PREFIX = "bd:study:section:";

/** Run controls and history stay on the Distill Agents stage. */
export function distillHref(bookId?: string | null, runId?: string | null): string {
  const params = new URLSearchParams();
  if (bookId) params.set(BOOK_PARAM, bookId);
  if (runId) params.set(DISTILL_RUN_PARAM, runId);
  const query = params.toString();
  return query ? `/bd/distill?${query}` : "/bd/distill";
}

/** A report citation opens the Distill results in Study at its source page. */
export function distillStudyHref(
  bookId?: string | null,
  runId?: string | null,
  page?: number | null,
): string {
  const params = new URLSearchParams();
  if (bookId) params.set(BOOK_PARAM, bookId);
  if (runId) params.set(DISTILL_RUN_PARAM, runId);
  if (page !== undefined && page !== null) params.set(PAGE_PARAM, String(page));
  const query = params.toString();
  return query ? `/bd/study?${query}` : "/bd/study";
}
