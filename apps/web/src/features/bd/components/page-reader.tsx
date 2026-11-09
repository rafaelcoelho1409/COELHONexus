"use client";

import { ChevronLeft, ChevronRight, X } from "lucide-react";
import Image from "next/image";
import { Children, isValidElement, type ReactNode, useId, useMemo, useState } from "react";
import type { Components } from "react-markdown";

import { Markdown } from "@/shared/markdown";
import { readerFont } from "@/shared/markdown/reader-font";

import { figureUrl } from "../api";
import { type BookStatus, type BookUnits, fmtBytes } from "../domain/books";
import { breadcrumb, type PageRow, pageChips, readableMarkdown } from "../domain/pages";
import { useModalDialog } from "../hooks/use-modal-dialog";
import type { PageContent, PageVersion } from "../hooks/use-page-content";
import { ActionButton } from "./action-button";
import { PageChanges } from "./page-changes";
import { PageQualityPanel } from "./page-quality";
import { StatusChip } from "./status-chip";
import { ToggleGroup } from "./toggle-group";

const PREVIEW_MODES = [
  { id: "rendered", label: "Rendered" },
  { id: "source", label: "Markdown source" },
] as const;
const VERSIONS: readonly { id: PageVersion; label: string }[] = [
  { id: "ai", label: "AI-processed" },
  { id: "docling", label: "Docling raw" },
  { id: "changes", label: "Changes" },
];
const EMPTY_FIGURES: readonly string[] = [];
/** One extracted content unit: its section path, what it contains, what the Agentic AI step did to it, its text and its
 * figures. The text itself (and the outline drawn beside it) comes from `usePageContent`, one level up, so the page, the
 * outline column and the reading window all show the same thing. The page frames it as a card; the reading window
 * shows it bare. */
export function PageReader({
  bookId,
  bookMetadata,
  page,
  row,
  nPages,
  units,
  content,
  framed = true,
  toolbar,
}: {
  bookId: string;
  bookMetadata?: {
    title: string;
    filename: string;
    status: BookStatus;
    sizeBytes: number;
    pageCount: number;
  };
  page: number | null;
  row: PageRow | undefined;
  nPages: number;
  units: BookUnits;
  content: PageContent;
  framed?: boolean;
  /** Buttons on the right of the header. */
  toolbar?: ReactNode;
}) {
  const [previewMode, setPreviewMode] = useState<(typeof PREVIEW_MODES)[number]["id"]>("rendered");
  const [selectedFigure, setSelectedFigure] = useState<number | null>(null);
  const figures = row?.figures ?? EMPTY_FIGURES;
  const { body, raw, hasAi, version, source } = content;
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

  const unitName = `${units.singular[0]?.toUpperCase()}${units.singular.slice(1)}`;
  const display = previewMode === "rendered" ? renderedText : source;
  const chips = pageChips(row);
  const trail = breadcrumb(row.sectionPath);
  const loadingVersion = version !== "ai" && raw.isPending && raw.fetchStatus !== "idle";

  return (
    <article
      aria-label={`${unitName} ${page}`}
      className={`min-w-0 ${framed ? "rounded-md border border-border bg-card p-4 sm:p-5" : ""}`}
    >
      <header className="mb-4 border-b border-border pb-4">
        {bookMetadata ? (
          <div className="min-w-0">
            <h4 className="break-words text-xl font-semibold">{bookMetadata.title}</h4>
            <p className="mt-1 break-all text-sm text-muted-foreground">{bookMetadata.filename}</p>
            <p className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted-foreground">
              <StatusChip status={bookMetadata.status} />
              <span>{fmtBytes(bookMetadata.sizeBytes)}</span>
              <span className="tabular">
                {bookMetadata.pageCount} {units.plural}
              </span>
            </p>
          </div>
        ) : null}
        <div
          className={`flex min-w-0 flex-wrap items-start justify-between gap-3 ${bookMetadata ? "mt-3" : ""}`}
        >
          <div className="min-w-0 flex-1">
            <p className="mono-label">
              {unitName} {page}
              {nPages > 0 ? ` of ${nPages}` : ""}
            </p>
            <p className="mt-1 text-xs text-muted-foreground" aria-live="polite">
              {hasAi
                ? version === "docling"
                  ? "Docling extraction (before Agentic AI)"
                  : version === "changes"
                    ? "Docling extraction compared with AI-processed content"
                    : "AI-processed content"
                : "Docling extraction"}
            </p>
          </div>
          {toolbar}
        </div>
        {trail ? <p className="mt-2 w-full break-words text-sm text-muted-foreground">{trail}</p> : null}
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
        <PageQualityPanel bookId={bookId} page={page} unit={unitName} enabled={hasAi} defaultOpen={framed} />
      </header>
      <div className="flex flex-wrap items-center justify-between gap-2">
        {version === "changes" ? (
          <span />
        ) : (
          <ToggleGroup
            legend="Content view"
            options={PREVIEW_MODES}
            value={previewMode}
            onChange={setPreviewMode}
          />
        )}
        {hasAi ? (
          <ToggleGroup
            legend="Version of this page"
            options={VERSIONS}
            value={version}
            onChange={content.setVersion}
          />
        ) : null}
      </div>
      <div className="mt-4">
        <section
          aria-label={
            version === "changes"
              ? "Changes"
              : previewMode === "rendered"
                ? "Rendered content"
                : "Markdown source"
          }
          className="min-w-0"
        >
          {version === "changes" ? (
            <PageChanges diff={content.diff} loading={loadingVersion} failed={raw.isError} />
          ) : display ? (
            previewMode === "rendered" ? (
              <div className={`${readerFont.className} reader-prose`}>
                <Markdown text={renderedText} components={markdownComponents} />
              </div>
            ) : (
              <pre className="break-words whitespace-pre-wrap font-mono text-xs text-foreground">
                {source}
              </pre>
            )
          ) : body.isError || (version === "docling" && raw.isError) ? (
            <p role="alert" className="text-sm text-destructive-text">
              This {units.singular} could not be loaded.
            </p>
          ) : body.isPending || loadingVersion ? (
            <p className="text-sm text-muted-foreground">Loading the {units.singular}…</p>
          ) : (
            <p className="text-sm text-muted-foreground">No readable text in this {units.singular}.</p>
          )}
        </section>
      </div>
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
        <ActionButton iconOnly aria-label="Close figure viewer" title="Close" onClick={onClose}>
          <X aria-hidden="true" />
        </ActionButton>
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
        <ActionButton aria-label="Previous figure" disabled={index === 0} onClick={() => onSelect(index - 1)}>
          <ChevronLeft aria-hidden="true" />
          Previous
        </ActionButton>
        <span className="text-xs tabular-nums text-muted-foreground">
          {index + 1} / {figures.length}
        </span>
        <ActionButton
          aria-label="Next figure"
          disabled={index === figures.length - 1}
          onClick={() => onSelect(index + 1)}
        >
          Next
          <ChevronRight aria-hidden="true" />
        </ActionButton>
      </nav>
    </dialog>
  );
}
