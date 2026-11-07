import { BOOK_PARAM } from "../params";

export const DISTILL_RUN_PARAM = "run";
export const DISTILL_RUN_CONTROLS_ENABLED = false;
export const DISTILL_REFRESH_MS = 4_000;

export function distillHref(bookId?: string | null, runId?: string | null): string {
  const params = new URLSearchParams();
  if (bookId) params.set(BOOK_PARAM, bookId);
  if (runId) params.set(DISTILL_RUN_PARAM, runId);
  const query = params.toString();
  return query ? `/bd/distill?${query}` : "/bd/distill";
}
