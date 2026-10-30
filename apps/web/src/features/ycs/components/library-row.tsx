"use client";

/** One ingested video in the Library: checkbox, thumbnail, status (an icon and a word), title link, where it comes from, what
 * was stored of it, and its own delete. Off-screen rows skip layout and paint, which keeps a page of fifty cheap. */

import { Trash2 } from "lucide-react";
import type { JSX } from "react";

import { Button } from "@/shared/ui/button";

import { libraryMetaLine, libraryStatsLine, libraryUrl } from "../domain";
import type { LibraryItem } from "../entities";
import { type BadgeTone, StatusBadge } from "./status-badge";

/** A library row's `status` as a badge tone: the three the backend reports, and anything else as waiting. */
function toneOf(status: string): BadgeTone {
  if (status === "done") return "done";
  if (status === "partial") return "partial";
  if (status === "error") return "failed";
  return "queued";
}

export function LibraryRow({
  row,
  checked,
  deleting,
  onToggle,
  onDelete,
}: {
  row: LibraryItem;
  checked: boolean;
  deleting: boolean;
  onToggle: (on: boolean, shiftKey: boolean) => void;
  onDelete: () => void;
}): JSX.Element {
  const title = row.title || row.video_id;
  const stats = libraryStatsLine(row);
  return (
    <li
      data-id={row.video_id}
      className={`grid grid-cols-[auto_auto_minmax(0,1fr)_auto] items-start gap-x-3 border-b border-border px-3 py-2 [contain-intrinsic-size:auto_80px] [content-visibility:auto] last:border-b-0 ${
        checked ? "bg-muted" : "hover:bg-muted/50"
      }`}
    >
      <label className="flex size-9 cursor-pointer items-center justify-center">
        <input
          type="checkbox"
          checked={checked}
          // The click is the one event that carries Shift (a change event does not), and Space on the box is a click too.
          readOnly
          onClick={(event) => onToggle(!checked, event.shiftKey)}
          aria-label={`Select ${title}`}
          className="size-4 cursor-pointer accent-primary"
        />
      </label>
      <span className="mt-0.5 block aspect-video w-24 shrink-0 overflow-hidden rounded-sm border border-border bg-muted">
        {row.thumbnail ? (
          // biome-ignore lint/performance/noImgElement: YouTube thumbnails come from changing CDN hosts
          <img src={row.thumbnail} alt="" loading="lazy" className="size-full object-cover" />
        ) : null}
      </span>
      <div className="min-w-0 pt-0.5">
        <div className="flex flex-wrap items-baseline gap-x-2 gap-y-0.5">
          <StatusBadge tone={toneOf(row.status)} label={row.status} className="capitalize" />
          <a
            href={libraryUrl(row)}
            target="_blank"
            rel="noopener noreferrer"
            title={title}
            className="min-w-0 py-1 text-sm leading-snug font-medium hover:text-primary-text hover:underline"
          >
            {row.title || "(no title)"}
            <span className="sr-only"> (opens YouTube in a new tab)</span>
          </a>
        </div>
        <p className="mt-0.5 text-xs break-words text-muted-foreground">{libraryMetaLine(row)}</p>
        {stats ? <p className="font-mono text-xs break-words text-muted-foreground">{stats}</p> : null}
      </div>
      <Button
        type="button"
        variant="ghost"
        size="icon-lg"
        onClick={onDelete}
        disabled={deleting}
        aria-label={`Delete ${title}`}
      >
        <Trash2 aria-hidden />
      </Button>
    </li>
  );
}
