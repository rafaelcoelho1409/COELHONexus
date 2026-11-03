import type { PageEntry } from "@/shared/api";

import type { LivePage } from "./run-state";

export type PageRow = {
  page: number;
  headings: number;
  tables: number;
  pictures: number;
  formulas: number;
  figures: string[];
  sectionPath: string[];
};

/** The extracted pages, ascending: what the manifest lists (after a reload) joined with what the stream has said
 * since. The stream is newer, so its values win for a page both know. */
export function mergePages(
  manifest: readonly PageEntry[],
  live: Readonly<Record<number, LivePage>>,
): PageRow[] {
  const byPage = new Map<number, PageRow>();
  for (const entry of manifest) {
    byPage.set(entry.page, {
      page: entry.page,
      headings: entry.headings,
      tables: entry.tables,
      pictures: entry.pictures,
      formulas: entry.formulas,
      figures: entry.figures,
      sectionPath: entry.section_path,
    });
  }
  for (const page of Object.values(live)) {
    byPage.set(page.page, {
      page: page.page,
      headings: page.stats.headings,
      tables: page.stats.tables,
      pictures: page.stats.pictures,
      formulas: page.stats.formulas,
      figures: page.figures,
      sectionPath: page.sectionPath,
    });
  }
  return [...byPage.values()].sort((a, b) => a.page - b.page);
}

const plural = (count: number, one: string, many: string) => `${count} ${count === 1 ? one : many}`;

/** The nonzero counts of a page as words ("2 tables"), so a chip never rests on a colour or an icon alone. */
export function pageChips(row: Pick<PageRow, "headings" | "tables" | "pictures" | "formulas">): string[] {
  const chips: string[] = [];
  if (row.headings > 0) chips.push(plural(row.headings, "heading", "headings"));
  if (row.tables > 0) chips.push(plural(row.tables, "table", "tables"));
  if (row.pictures > 0) chips.push(plural(row.pictures, "picture", "pictures"));
  if (row.formulas > 0) chips.push(plural(row.formulas, "formula", "formulas"));
  return chips;
}

/** The converter leaves HTML comments in the markdown where a picture was (`<!-- image -->`). Raw HTML is not rendered, so
 * they would show as text: take them out. */
export function readableMarkdown(text: string): string {
  return text
    .replace(/<!--[\s\S]*?-->/g, "")
    .replace(/\n{3,}/g, "\n\n")
    .trim();
}

const BREADCRUMB_SEPARATOR = " › ";

/** "Part I › Chapter 2 › Limits", or an empty string for a page before the first heading. */
export function breadcrumb(sectionPath: readonly string[]): string {
  return sectionPath.join(BREADCRUMB_SEPARATOR);
}

/** The page to show: the newest while following; otherwise the one picked, or the first one extracted. */
export function shownPage(
  { follow, picked }: { follow: boolean; picked: number | null },
  newest: number | null,
  rows: readonly PageRow[],
): number | null {
  if (follow) return newest ?? rows.at(-1)?.page ?? null;
  return picked ?? rows[0]?.page ?? null;
}
