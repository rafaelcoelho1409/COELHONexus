"use client";

import Image from "next/image";
import { useState } from "react";

import { Markdown } from "@/shared/markdown";

import { figureUrl } from "../api";
import type { BookUnits } from "../domain/books";
import { breadcrumb, type PageRow, pageChips, readableMarkdown } from "../domain/pages";
import type { LivePage } from "../domain/run-state";
import { usePageBody } from "../hooks/use-bd-data";

const PREVIEW_MODES = [
  { id: "rendered", label: "Rendered" },
  { id: "source", label: "Markdown source" },
] as const;

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
  readingMode = false,
}: {
  bookId: string;
  page: number | null;
  row: PageRow | undefined;
  livePage: LivePage | undefined;
  nPages: number;
  units: BookUnits;
  readingMode?: boolean;
}) {
  const [previewMode, setPreviewMode] = useState<(typeof PREVIEW_MODES)[number]["id"]>("rendered");
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

  const source = body.data ?? livePage?.markdown ?? "";
  const text = readableMarkdown(source);
  const content = previewMode === "rendered" ? text : source;
  const chips = pageChips(row);
  const trail = breadcrumb(row.sectionPath);

  return (
    <article
      aria-label={`${units.singular[0]?.toUpperCase()}${units.singular.slice(1)} ${page}`}
      className={`min-w-0 ${readingMode ? "" : "rounded-md border border-border bg-card p-4 sm:p-5"}`}
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
      <div className="mt-4 flex justify-end">
        <fieldset className="m-0 flex min-w-0 border-0 p-0">
          <legend className="sr-only">Content view</legend>
          {PREVIEW_MODES.map((mode) => (
            <button
              key={mode.id}
              type="button"
              aria-pressed={previewMode === mode.id}
              onClick={() => setPreviewMode(mode.id)}
              className={`min-h-9 border px-3 text-xs first:rounded-l-sm last:rounded-r-sm ${
                previewMode === mode.id
                  ? "border-primary bg-primary/10 text-primary-text"
                  : "-ml-px border-border text-muted-foreground hover:text-foreground"
              }`}
            >
              {mode.label}
            </button>
          ))}
        </fieldset>
      </div>
      <section
        aria-label={previewMode === "rendered" ? "Rendered content" : "Markdown source"}
        className="mt-2 min-w-0"
      >
        {content ? (
          previewMode === "rendered" ? (
            <Markdown text={text} />
          ) : (
            <pre className="break-words whitespace-pre-wrap font-mono text-xs text-foreground">{source}</pre>
          )
        ) : body.isError ? (
          <p role="alert" className="text-sm text-destructive-text">
            This {units.singular} could not be loaded.
          </p>
        ) : body.isPending ? (
          <p className="text-sm text-muted-foreground">Loading the {units.singular}…</p>
        ) : (
          <p className="text-sm text-muted-foreground">No readable text in this {units.singular}.</p>
        )}
        {livePage?.truncated && body.isPending && source ? (
          <p className="mt-2 text-xs text-muted-foreground">Loading the rest of the {units.singular}…</p>
        ) : null}
      </section>
      {row.figures.length > 0 ? (
        <details open={row.figures.length <= 4} className="mt-4 rounded-sm border border-border p-2">
          <summary className="flex min-h-9 cursor-pointer items-center text-sm font-medium">
            Figures · {row.figures.length}
          </summary>
          <ul aria-label="Figures" className="mt-2 grid grid-cols-1 gap-3 sm:grid-cols-2">
            {row.figures.map((name) => (
              <li key={name} className="min-w-0 border border-border p-2">
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
        </details>
      ) : null}
    </article>
  );
}
