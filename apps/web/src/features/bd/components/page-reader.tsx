"use client";

import Image from "next/image";

import { Markdown } from "@/shared/markdown";

import { figureUrl } from "../api";
import type { BookUnits } from "../domain/books";
import { breadcrumb, type PageRow, pageChips, readableMarkdown } from "../domain/pages";
import type { LivePage } from "../domain/run-state";
import { usePageBody } from "../hooks/use-bd-data";

/** One extracted content unit: its section path, what it contains, its markdown and its figures. The text comes from the
 * stream while it is complete; a cut page (`truncated`) and a page known only from the manifest (after a reload)
 * are read from `GET …/pages/{n}`. */
export function PageReader({
  bookId,
  page,
  row,
  livePage,
  nPages,
  units,
}: {
  bookId: string;
  page: number | null;
  row: PageRow | undefined;
  livePage: LivePage | undefined;
  nPages: number;
  units: BookUnits;
}) {
  const needsBody =
    row !== undefined && (livePage === undefined || livePage.truncated || livePage.markdown === "");
  const body = usePageBody(bookId, page, needsBody);

  if (page === null || row === undefined) {
    return (
      <section
        aria-label={`${units.singular[0]?.toUpperCase()}${units.singular.slice(1)} reader`}
        className="rounded-md border border-border bg-card p-6 text-sm text-muted-foreground"
      >
        {units.plural[0]?.toUpperCase()}
        {units.plural.slice(1)} appear here as they are read. Choose one in the list to read it.
      </section>
    );
  }

  const text = readableMarkdown(body.data ?? livePage?.markdown ?? "");
  const chips = pageChips(row);
  const trail = breadcrumb(row.sectionPath);

  return (
    <article
      aria-label={`${units.singular[0]?.toUpperCase()}${units.singular.slice(1)} ${page}`}
      className="min-w-0 rounded-md border border-border bg-card p-4 sm:p-5"
    >
      <header>
        <p className="mono-label">
          {units.singular[0]?.toUpperCase()}
          {units.singular.slice(1)} {page}
          {nPages > 0 ? ` of ${nPages}` : ""}
        </p>
        {trail ? <p className="mt-1 text-sm text-muted-foreground">{trail}</p> : null}
        {chips.length > 0 ? (
          <ul aria-label={`What the ${units.singular} contains`} className="mt-2 flex flex-wrap gap-2">
            {chips.map((chip) => (
              <li
                key={chip}
                className="rounded-sm border border-border px-2 py-0.5 text-xs text-muted-foreground"
              >
                {chip}
              </li>
            ))}
          </ul>
        ) : null}
      </header>
      <div className="mt-4 min-w-0">
        {text ? (
          <Markdown text={text} />
        ) : body.isError ? (
          <p role="alert" className="text-sm text-destructive-text">
            This {units.singular} could not be loaded.
          </p>
        ) : (
          <p className="text-sm text-muted-foreground">Loading the {units.singular}…</p>
        )}
        {livePage?.truncated && body.isPending && text ? (
          <p className="mt-2 text-xs text-muted-foreground">Loading the rest of the {units.singular}…</p>
        ) : null}
      </div>
      {row.figures.length > 0 ? (
        <ul aria-label="Figures" className="mt-4 grid grid-cols-1 gap-3 sm:grid-cols-2">
          {row.figures.map((name) => (
            <li key={name} className="min-w-0 border border-border p-2">
              {/* The figure is a PNG the server cropped from the page; its size is not known here. */}
              <Image
                src={figureUrl(bookId, name)}
                alt={`Figure ${name} from ${units.singular} ${page}`}
                width={640}
                height={480}
                unoptimized
                className="h-auto w-full"
              />
              <p className="mt-1 break-all font-mono text-xs text-muted-foreground">{name}</p>
            </li>
          ))}
        </ul>
      ) : null}
    </article>
  );
}
