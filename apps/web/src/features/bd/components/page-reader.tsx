"use client";

import { ChevronLeft, ChevronRight, X } from "lucide-react";
import Image from "next/image";
import { Children, isValidElement, useId, useMemo, useState } from "react";
import type { Components } from "react-markdown";

import { Markdown } from "@/shared/markdown";

import { figureUrl } from "../api";
import type { BookUnits } from "../domain/books";
import { breadcrumb, type PageRow, pageChips, readableMarkdown } from "../domain/pages";
import type { LivePage } from "../domain/run-state";
import { usePageBody } from "../hooks/use-bd-data";
import { useModalDialog } from "../hooks/use-modal-dialog";

const PREVIEW_MODES = [
  { id: "rendered", label: "Rendered" },
  { id: "source", label: "Markdown source" },
] as const;
const EMPTY_FIGURES: readonly string[] = [];

/** One extracted content unit: its section path, what it contains, its markdown and its figures. The text comes from the
 * stream while it is complete; a cut page (`truncated`) and a page known only from the manifest (after a reload)
 * are read from `GET …/pages/{n}`. */
export function PageReader({
  bookId,
  page,
  row,
  livePage,
  agenticRunAvailable = false,
  nPages,
  units,
  readingMode = false,
}: {
  bookId: string;
  page: number | null;
  row: PageRow | undefined;
  livePage: LivePage | undefined;
  agenticRunAvailable?: boolean;
  nPages: number;
  units: BookUnits;
  readingMode?: boolean;
}) {
  const [previewMode, setPreviewMode] = useState<(typeof PREVIEW_MODES)[number]["id"]>("rendered");
  const needsBody =
    row !== undefined &&
    (agenticRunAvailable || livePage === undefined || livePage.truncated || livePage.markdown === "");
  const body = usePageBody(bookId, page, needsBody);
  const [selectedFigure, setSelectedFigure] = useState<number | null>(null);
  const figures = row?.figures ?? EMPTY_FIGURES;
  const source = body.data?.markdown ?? livePage?.markdown ?? "";
  const contentSource = body.data?.content_source ?? "docling";
  const renderedText = useMemo(
    () => inlineFigures(source, figures, bookId, page ?? 0, units.singular),
    [source, figures, bookId, page, units.singular],
  );
  const markdownComponents = useMemo<Components>(
    () => ({
      p: ({ children }) => {
        const paragraphChildren = Children.toArray(children);
        const image = paragraphChildren.length === 1 ? paragraphChildren[0] : undefined;
        if (!isValidElement<{ src?: string }>(image) || typeof image.props.src !== "string") {
          return <p>{children}</p>;
        }
        const figureIndex = figures.findIndex((name) => figureUrl(bookId, name) === image.props.src);
        const name = figureIndex < 0 ? undefined : figures[figureIndex];
        if (name === undefined || figureIndex < 0) return <p>{children}</p>;
        return (
          <InlineFigurePreview
            bookId={bookId}
            name={name}
            index={figureIndex}
            location={`${units.singular} ${page ?? 0}`}
            onOpen={() => setSelectedFigure(figureIndex)}
            label={`figure ${figureIndex + 1} from ${units.singular} ${page ?? 0}`}
          />
        );
      },
    }),
    [bookId, figures, page, units.singular],
  );

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

  const content = previewMode === "rendered" ? renderedText : source;
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
        <p className="mt-1 text-xs text-muted-foreground" aria-live="polite">
          {contentSource === "agentic" ? "AI-processed content" : "Docling extraction"}
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
            <Markdown text={renderedText} components={markdownComponents} />
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
        <details className="mt-4 rounded-sm border border-border p-2">
          <summary className="flex min-h-9 cursor-pointer items-center text-sm font-medium">
            Figure gallery · {row.figures.length}
          </summary>
          <ul aria-label="Figures" className="mt-2 grid grid-cols-1 gap-3 sm:grid-cols-2">
            {row.figures.map((name, index) => (
              <li key={name} className="min-w-0 border border-border p-2">
                <FigurePreview
                  bookId={bookId}
                  name={name}
                  index={index}
                  location={`${units.singular} ${page}`}
                  onOpen={() => setSelectedFigure(index)}
                  label={`figure ${index + 1} from ${units.singular} ${page}`}
                />
              </li>
            ))}
          </ul>
        </details>
      ) : null}
      {selectedFigure !== null && row.figures[selectedFigure] !== undefined ? (
        <FigureViewer
          figures={row.figures}
          index={selectedFigure}
          location={`${units.singular} ${page}`}
          bookId={bookId}
          onSelect={setSelectedFigure}
          onClose={() => setSelectedFigure(null)}
        />
      ) : null}
    </article>
  );
}

function inlineFigures(
  markdown: string,
  figures: readonly string[],
  bookId: string,
  page: number,
  unit: BookUnits["singular"],
): string {
  let index = 0;
  const withFigures = markdown.replace(/<!--[\s]*image[\s]*-->/gi, () => {
    const figure = figures[index];
    const figureNumber = index + 1;
    index += 1;
    if (figure === undefined) return "";
    const alt = `Extracted figure ${figureNumber} from ${unit} ${page}`;
    return `\n\n![${alt}](${figureUrl(bookId, figure)})\n\n`;
  });
  return readableMarkdown(withFigures);
}

function FigurePreview({
  bookId,
  name,
  index,
  location,
  onOpen,
  label,
}: {
  bookId: string;
  name: string;
  index: number;
  location: string;
  onOpen: () => void;
  label: string;
}) {
  const accessibleLabel = `View ${label} (${name})`;
  return (
    <figure aria-label={`Figure ${index + 1} from ${location}`} className="min-w-0">
      <button
        type="button"
        aria-label={accessibleLabel}
        onClick={onOpen}
        className="block min-h-9 w-full rounded-sm border border-border bg-background p-2 hover:border-neutral-8 focus-visible:outline-2 focus-visible:outline-ring"
      >
        <Image
          src={figureUrl(bookId, name)}
          alt=""
          width={640}
          height={480}
          unoptimized
          sizes="(max-width: 640px) 100vw, 50vw"
          className="mx-auto h-auto max-h-[30rem] max-w-full object-contain"
        />
      </button>
      <figcaption className="mt-1 min-w-0 text-xs text-muted-foreground">
        <span>Figure {index + 1} · </span>
        <span className="break-all font-mono">{name}</span>
        <span> · {location}</span>
      </figcaption>
    </figure>
  );
}

function InlineFigurePreview({
  bookId,
  name,
  index,
  location,
  onOpen,
  label,
}: {
  bookId: string;
  name: string;
  index: number;
  location: string;
  onOpen: () => void;
  label: string;
}) {
  return (
    <figure aria-label={`Figure ${index + 1} from ${location}`} className="my-5 max-w-3xl">
      <button
        type="button"
        aria-label={`View ${label} (${name})`}
        onClick={onOpen}
        className="block min-h-9 w-full rounded-sm border border-border bg-background p-2 hover:border-neutral-8 focus-visible:outline-2 focus-visible:outline-ring"
      >
        <Image
          src={figureUrl(bookId, name)}
          alt=""
          width={640}
          height={480}
          unoptimized
          sizes="(max-width: 640px) 100vw, 50vw"
          className="mx-auto h-auto max-h-[30rem] max-w-full object-contain"
        />
      </button>
      <figcaption className="mt-1 min-w-0 text-xs text-muted-foreground">
        <span>Figure {index + 1} · </span>
        <span className="break-all font-mono">{name}</span>
        <span> · {location}</span>
      </figcaption>
    </figure>
  );
}

function FigureViewer({
  figures,
  index,
  location,
  bookId,
  onSelect,
  onClose,
}: {
  figures: readonly string[];
  index: number;
  location: string;
  bookId: string;
  onSelect: (index: number) => void;
  onClose: () => void;
}) {
  const dialogRef = useModalDialog();
  const titleId = useId();
  const [failedImage, setFailedImage] = useState<string | null>(null);
  const name = figures[index];
  if (name === undefined) return null;

  return (
    <dialog
      ref={dialogRef}
      aria-labelledby={titleId}
      onCancel={(event) => {
        event.preventDefault();
        onClose();
      }}
      className="m-auto max-h-[calc(100dvh-2rem)] w-[min(92vw,80rem)] max-w-none overflow-auto rounded-md border border-border bg-card p-3 text-foreground backdrop:bg-background/80 sm:p-5"
    >
      <header className="mb-3 flex items-start gap-3">
        <div className="min-w-0 flex-1">
          <h2 id={titleId} className="font-medium">
            Figure {index + 1} of {figures.length}
          </h2>
          <p className="break-all text-xs text-muted-foreground">
            {location} · <span className="font-mono">{name}</span>
          </p>
        </div>
        <button
          type="button"
          aria-label="Close figure viewer"
          title="Close"
          onClick={onClose}
          className="inline-flex size-9 shrink-0 items-center justify-center rounded-sm text-muted-foreground hover:text-foreground focus-visible:outline-2 focus-visible:outline-ring"
        >
          <X className="size-4" aria-hidden="true" />
        </button>
      </header>
      {failedImage === name ? (
        <p role="alert" className="border border-border p-4 text-sm text-destructive-text">
          This extracted figure could not be loaded.
        </p>
      ) : (
        <figure>
          <div className="flex min-h-48 items-center justify-center overflow-hidden border border-border bg-background p-2">
            <Image
              src={figureUrl(bookId, name)}
              alt={`No description available for figure ${index + 1} from ${location}.`}
              width={1600}
              height={1200}
              unoptimized
              sizes="(max-width: 768px) 92vw, 80vw"
              onError={() => setFailedImage(name)}
              className="mx-auto h-auto max-h-[70dvh] max-w-full object-contain"
            />
          </div>
          <figcaption className="mt-2 text-xs text-muted-foreground">
            No figure description was extracted.
          </figcaption>
        </figure>
      )}
      <nav aria-label="Figure navigation" className="mt-3 flex items-center justify-between gap-2">
        <button
          type="button"
          aria-label="Previous figure"
          disabled={index === 0}
          onClick={() => onSelect(index - 1)}
          className="inline-flex min-h-9 items-center gap-1 rounded-sm border border-border px-3 text-sm disabled:opacity-50"
        >
          <ChevronLeft className="size-4" aria-hidden="true" />
          Previous
        </button>
        <span className="text-xs tabular-nums text-muted-foreground">
          {index + 1} / {figures.length}
        </span>
        <button
          type="button"
          aria-label="Next figure"
          disabled={index === figures.length - 1}
          onClick={() => onSelect(index + 1)}
          className="inline-flex min-h-9 items-center gap-1 rounded-sm border border-border px-3 text-sm disabled:opacity-50"
        >
          Next
          <ChevronRight className="size-4" aria-hidden="true" />
        </button>
      </nav>
    </dialog>
  );
}
