/** The Study tab's layout preference: which side columns are open and how wide the text runs. Stored as JSON in the
 * browser (see `useStudyLayout`); reading it never throws and never returns something unusable. */

import { type StudyLayout, studyLayoutSchema } from "../entities";

export const DEFAULT_STUDY_LAYOUT: StudyLayout = studyLayoutSchema.parse({});

/** The layout a stored string describes: the default for nothing, for text that is not JSON, or for a field that is wrong. */
export function parseStudyLayout(raw: string | null): StudyLayout {
  if (!raw) return DEFAULT_STUDY_LAYOUT;
  try {
    const parsed = studyLayoutSchema.safeParse(JSON.parse(raw));
    return parsed.success ? parsed.data : DEFAULT_STUDY_LAYOUT;
  } catch {
    return DEFAULT_STUDY_LAYOUT; // not JSON
  }
}

/** `layout` with `patch` applied, as the string to store. */
export function storeStudyLayout(layout: StudyLayout, patch: Partial<StudyLayout>): string {
  return JSON.stringify({ ...layout, ...patch });
}
