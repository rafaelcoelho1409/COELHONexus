import type { PageEntry } from "@/shared/api";

import type { AgenticRunStatus } from "../entities";
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

/** Search extracted units by number, section trail, or the structure they contain. */
export function filterPageRows(rows: readonly PageRow[], query: string): PageRow[] {
  const needle = query.trim().toLowerCase();
  if (!needle) return [...rows];

  return rows.filter((row) =>
    [String(row.page), breadcrumb(row.sectionPath), ...pageChips(row)]
      .join(" ")
      .toLowerCase()
      .includes(needle),
  );
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

/** A page-list state for known review work, an unverified audit, or a page left out of delivered text. */
export type PageFlag = "review" | "unverified" | "blank" | "image";

export const PAGE_FLAG_LABELS: Record<PageFlag, string> = {
  review: "Needs review",
  unverified: "Audit unverified",
  blank: "Blank · not delivered",
  image: "Image only",
};

/** The flagged pages of a run. Known review work takes priority when a page is also unverified. */
export function pageFlags(
  run: Pick<
    AgenticRunStatus,
    "review_pages" | "unverified_pages" | "discarded_pages" | "image_only_pages"
  > | null,
): Map<number, PageFlag> {
  const flags = new Map<number, PageFlag>();
  if (run === null) return flags;
  for (const page of run.image_only_pages) flags.set(page, "image");
  for (const page of run.discarded_pages) flags.set(page, "blank");
  for (const page of run.unverified_pages) flags.set(page, "unverified");
  for (const page of run.review_pages) flags.set(page, "review");
  return flags;
}

/** The next (or previous) page that needs review, wrapping around the book; null when none needs it other than the one shown. */
export function neighbourReviewPage(
  reviewPages: readonly number[],
  current: number | null,
  direction: 1 | -1,
): number | null {
  const sorted = [...reviewPages].sort((a, b) => a - b);
  const others = sorted.filter((page) => page !== current);
  if (others.length === 0) return null;
  const after = current ?? (direction === 1 ? Number.NEGATIVE_INFINITY : Number.POSITIVE_INFINITY);
  const ahead =
    direction === 1
      ? others.find((page) => page > after)
      : [...others].reverse().find((page) => page < after);
  return ahead ?? (direction === 1 ? (others[0] ?? null) : (others.at(-1) ?? null));
}
