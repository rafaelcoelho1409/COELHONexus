import { BOOK_PARAM, PAGE_PARAM } from "../params";

export const DISTILL_RUN_PARAM = "run";
export const STUDY_VIEW_PARAM = "study";
export const DISTILL_REFRESH_MS = 4_000;
export const DISTILL_CANCEL_RETRY_MS = 10_000;
export const STUDY_LAST_SECTION_STORAGE_PREFIX = "bd:study:section:";

export type StudyViewId = "guide" | "selection";

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
  studyView: StudyViewId = "guide",
): string {
  const params = new URLSearchParams();
  if (bookId) params.set(BOOK_PARAM, bookId);
  if (runId) params.set(DISTILL_RUN_PARAM, runId);
  if (page !== undefined && page !== null) params.set(PAGE_PARAM, String(page));
  if (studyView === "selection") params.set(STUDY_VIEW_PARAM, studyView);
  const query = params.toString();
  return query ? `/bd/study?${query}` : "/bd/study";
}
