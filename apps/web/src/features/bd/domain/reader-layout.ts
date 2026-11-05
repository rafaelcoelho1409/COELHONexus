/** The Materials reader's layout preference: which side columns are open and how wide the text runs. Stored as JSON in
 * the browser (see `useReaderLayout`); reading it never throws and never returns something unusable. */

import { type ReaderLayout, readerLayoutSchema } from "../entities";

export const DEFAULT_READER_LAYOUT: ReaderLayout = readerLayoutSchema.parse({});

/** The layout a stored string describes: the default for nothing, for text that is not JSON, or for a field that is wrong. */
export function parseReaderLayout(raw: string | null): ReaderLayout {
  if (!raw) return DEFAULT_READER_LAYOUT;
  try {
    const parsed = readerLayoutSchema.safeParse(JSON.parse(raw));
    return parsed.success ? parsed.data : DEFAULT_READER_LAYOUT;
  } catch {
    return DEFAULT_READER_LAYOUT; // not JSON
  }
}

/** `layout` with `patch` applied, as the string to store. */
export function storeReaderLayout(layout: ReaderLayout, patch: Partial<ReaderLayout>): string {
  return JSON.stringify({ ...layout, ...patch });
}

/** The page a book was left on: the stored number when it is a page the book has, otherwise nothing. */
export function parseRememberedPage(raw: string | null, pageCount: number): number | null {
  if (!raw) return null;
  const page = Number(raw);
  return Number.isInteger(page) && page >= 1 && page <= pageCount ? page : null;
}
