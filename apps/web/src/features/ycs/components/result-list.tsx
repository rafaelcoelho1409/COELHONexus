"use client";

/** The rows of the Search results: one checkbox, a thumbnail, the title and meta, and a copy-ID control per result.
 * Neutral surfaces and hairlines only — the kind is said in words and an icon, never by colour. */

import { Check, Copy, ListVideo, User, Video } from "lucide-react";
import { type JSX, useState } from "react";

import { durationLabel, resultMeta } from "../domain";
import type { VideoSnippet } from "../entities";

export type Density = "compact" | "comfortable";

const KIND_LABEL = { video: "Video", channel: "Channel", playlist: "Playlist" } as const;
type Kind = keyof typeof KIND_LABEL;

const kindOf = (snippet: VideoSnippet): Kind =>
  snippet.kind === "channel" || snippet.kind === "playlist" ? snippet.kind : "video";

function KindIcon({ kind, className }: { kind: Kind; className: string }): JSX.Element {
  if (kind === "channel") return <User aria-hidden className={className} />;
  if (kind === "playlist") return <ListVideo aria-hidden className={className} />;
  return <Video aria-hidden className={className} />;
}

/** Row grid: checkbox | thumbnail | text, and the copy-ID control on the right from 640px up (under the text below).
 * Off-screen rows skip layout and paint (`content-visibility`), which is what keeps 200 rows cheap without windowing. */
const ROW_GRID =
  "grid grid-cols-[auto_auto_minmax(0,1fr)] items-start gap-x-3 gap-y-1 border-b border-border px-3 [contain-intrinsic-size:auto_88px] [content-visibility:auto] last:border-b-0 sm:grid-cols-[auto_auto_minmax(0,1fr)_auto]";

const THUMB_SIZE: Record<Density, string> = { compact: "w-24", comfortable: "w-40" };
const ROW_PAD: Record<Density, string> = { compact: "py-1.5", comfortable: "py-3" };

/** One result row. `data-id` lets tests and the Shift-range finder locate it. */
export function ResultRow({
  snippet,
  checked,
  density,
  onCheck,
  onCopyId,
}: {
  snippet: VideoSnippet;
  checked: boolean;
  density: Density;
  onCheck: (checked: boolean, shiftKey: boolean) => void;
  onCopyId: () => void;
}): JSX.Element {
  const [copied, setCopied] = useState(false);
  const kind = kindOf(snippet);
  const duration = kind === "video" ? durationLabel(snippet) : null;
  const title = snippet.title ?? "(no title)";

  const copy = () => {
    onCopyId();
    setCopied(true);
    setTimeout(() => setCopied(false), 1800);
  };

  return (
    <li
      data-id={snippet.id}
      data-kind={kind}
      className={`${ROW_GRID} ${ROW_PAD[density]} ${checked ? "bg-muted" : "hover:bg-muted/50"}`}
    >
      {/* The label is the 36px target; the checkbox is named for the screen reader by its own aria-label. */}
      <label className="flex size-9 cursor-pointer items-center justify-center">
        <input
          type="checkbox"
          checked={checked}
          // The click is the one event that carries Shift (a change event does not), and Space on the box is a click too.
          readOnly
          onClick={(event) => onCheck(!checked, event.shiftKey)}
          aria-label={`Select ${title}`}
          className="size-4 cursor-pointer accent-primary"
        />
      </label>

      {/* The same link as the title, so it is hidden from assistive technology and is not a second tab stop. */}
      {/* biome-ignore lint/a11y/useAnchorContent: a duplicate of the title link, deliberately hidden (aria-hidden + tabIndex -1) so it is read and tabbed to once */}
      <a
        href={snippet.url}
        target="_blank"
        rel="noopener noreferrer"
        tabIndex={-1}
        aria-hidden
        className={`relative mt-0.5 block aspect-video shrink-0 overflow-hidden rounded-sm border border-border bg-muted ${THUMB_SIZE[density]}`}
      >
        {snippet.thumbnail ? (
          // biome-ignore lint/performance/noImgElement: YouTube thumbnails come from changing CDN hosts
          <img src={snippet.thumbnail} alt="" loading="lazy" className="size-full object-cover" />
        ) : (
          <span className="flex size-full items-center justify-center text-muted-foreground">
            <KindIcon kind={kind} className="size-6" />
          </span>
        )}
        {duration ? (
          <span className="absolute right-1 bottom-1 rounded-sm bg-foreground px-1 py-0.5 font-mono text-xs leading-none text-background">
            {duration}
          </span>
        ) : null}
      </a>

      <div className="min-w-0 pt-0.5">
        {/* The kind sits above the title on a phone (the title needs the width) and beside it from 640px up. */}
        <div className="flex flex-col items-start gap-1 sm:flex-row sm:gap-2">
          <span className="mono-label mt-0.5 inline-flex shrink-0 items-center gap-1 rounded-sm border border-border px-1.5 py-0.5 leading-none">
            <KindIcon kind={kind} className="size-3" />
            {KIND_LABEL[kind]}
          </span>
          <a
            href={snippet.url}
            target="_blank"
            rel="noopener noreferrer"
            className={`min-w-0 text-sm leading-snug font-medium hover:text-primary-text hover:underline ${
              density === "compact" ? "line-clamp-2 sm:line-clamp-1" : "line-clamp-2"
            }`}
          >
            {title}
            <span className="sr-only"> (opens YouTube in a new tab)</span>
          </a>
        </div>
        <p className="mt-1 font-mono text-xs text-muted-foreground">{resultMeta(snippet)}</p>
        {snippet.description ? (
          <p
            className={`mt-1 text-xs text-muted-foreground ${density === "compact" ? "line-clamp-1" : "line-clamp-2"}`}
          >
            {snippet.description}
          </p>
        ) : null}
      </div>

      <button
        type="button"
        onClick={copy}
        title={copied ? "Copied to the clipboard" : `Copy ID ${snippet.id}`}
        aria-label={snippet.id}
        className="col-start-3 inline-flex min-h-9 items-center gap-1.5 justify-self-start rounded-sm border border-border px-2 font-mono text-xs text-muted-foreground hover:bg-muted hover:text-foreground sm:col-start-auto"
      >
        {copied ? (
          <Check aria-hidden className="size-3.5 text-success-text" />
        ) : (
          <Copy aria-hidden className="size-3.5" />
        )}
        <span>{copied ? "Copied" : snippet.id}</span>
      </button>
    </li>
  );
}

/** Placeholder rows while the first page loads, the same shape as a row so nothing jumps. */
export function ResultSkeleton({ density }: { density: Density }): JSX.Element {
  return (
    <li aria-hidden className={`${ROW_GRID} ${ROW_PAD[density]} animate-status`}>
      <span className="size-9" />
      <span className={`aspect-video rounded-sm bg-muted ${THUMB_SIZE[density]}`} />
      <span className="flex flex-col gap-2 pt-1.5">
        <span className="h-4 w-3/5 rounded-sm bg-muted" />
        <span className="h-3 w-2/5 rounded-sm bg-muted" />
      </span>
    </li>
  );
}
