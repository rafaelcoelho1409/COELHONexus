"use client";

/** The picker: what a channel, a playlist or a pasted batch holds, to tick the videos to ingest. A header (what the source is
 * and how much of it is loaded), a toolbar that sticks under the app header (tri-state master checkbox, the one selection count,
 * a filter), the rows in the page's own flow — no scroll box inside the page — and Load more. The submit and ingest-all actions
 * live in the tab's bar, next to the options they apply to. */

import type { JSX } from "react";

import { durationLabel, fmtCount, fmtDate, idsMasterState } from "../domain";
import type { VideoSnippet } from "../entities";
import type { Picker } from "../hooks/use-picker";
import { PICKER_PAGE_SIZE } from "../params";

/** Row grid: checkbox | thumbnail | text. Off-screen rows skip layout and paint, which keeps hundreds of rows cheap. */
const ROW_GRID =
  "grid grid-cols-[auto_auto_minmax(0,1fr)] items-start gap-x-3 border-b border-border px-3 py-2 [contain-intrinsic-size:auto_72px] [content-visibility:auto] last:border-b-0";

export function PickerView({
  picker,
  fallbackTitle,
}: {
  picker: Picker;
  /** The heading when the source has no title of its own (a pasted batch). */
  fallbackTitle: string;
}): JSX.Element {
  const visible = picker.filtered;
  const master = idsMasterState(
    visible.map((v) => v.id),
    picker.selected,
  );
  const loadedText =
    picker.total != null
      ? `${picker.items.length} of ${fmtCount(picker.total)} loaded`
      : `${picker.items.length} loaded`;
  const firstLoad = picker.loading && picker.items.length === 0;

  return (
    <section aria-label="Videos to ingest" className="rounded-md border border-border bg-card">
      <div className="flex flex-wrap items-end justify-between gap-x-4 gap-y-1 rounded-t-md border-b border-border px-3 py-3">
        <div className="min-w-0">
          <h3 className="truncate text-sm font-medium">{picker.title ?? fallbackTitle}</h3>
          {picker.channel ? <p className="truncate text-xs text-muted-foreground">{picker.channel}</p> : null}
        </div>
        <p className="tabular font-mono text-xs text-muted-foreground">
          {picker.total != null ? `${fmtCount(picker.total)} videos · ` : ""}
          {loadedText}
        </p>
      </div>

      <div className="z-10 flex flex-wrap items-center gap-x-4 gap-y-2 border-b border-border bg-card px-3 py-2 sm:sticky sm:top-14">
        <label className="flex min-h-9 cursor-pointer items-center gap-3 text-sm">
          <span className="flex size-9 items-center justify-center">
            <input
              type="checkbox"
              ref={(element) => {
                if (element) element.indeterminate = master === "some";
              }}
              checked={master === "all"}
              disabled={visible.length === 0}
              onChange={(event) =>
                picker.setAll(
                  visible.map((v) => v.id),
                  event.currentTarget.checked,
                )
              }
              aria-label="Select all loaded videos"
              className="size-4 cursor-pointer accent-primary"
            />
          </span>
          Select all loaded
        </label>
        <p role="status" className="tabular text-xs text-muted-foreground">
          <strong className="font-semibold text-foreground">{picker.selected.size}</strong> selected
        </p>
        <input
          type="search"
          value={picker.filterQ}
          onChange={(event) => picker.setFilterQ(event.currentTarget.value)}
          placeholder="Filter by title or channel…"
          aria-label="Filter loaded videos"
          autoComplete="off"
          className="ml-auto h-9 w-full rounded-sm border border-input bg-background px-2.5 text-sm outline-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50 sm:w-64"
        />
      </div>

      <ul aria-label="Source videos" aria-busy={picker.loading}>
        {visible.map((snippet) => (
          <PickerRow
            key={snippet.id}
            snippet={snippet}
            checked={picker.selected.has(snippet.id)}
            showChannel={!picker.channel}
            onToggle={picker.toggle}
          />
        ))}
        {firstLoad ? SKELETON_KEYS.map((key) => <PickerSkeleton key={key} />) : null}
        {!firstLoad && picker.loading
          ? SKELETON_KEYS.slice(0, 2).map((key) => <PickerSkeleton key={key} />)
          : null}
        {!picker.loading && visible.length === 0 ? (
          <li className="px-3 py-6 text-sm text-muted-foreground">
            {picker.items.length === 0
              ? "Nothing loaded yet."
              : `No loaded video matches “${picker.filterQ.trim()}”. Clear the filter to see all ${picker.items.length}.`}
          </li>
        ) : null}
      </ul>

      {picker.hasMore || picker.loading ? (
        <div className="flex flex-wrap items-center gap-x-4 gap-y-1 rounded-b-md border-t border-border px-3 py-2">
          <button
            type="button"
            onClick={() => void picker.loadMore()}
            disabled={picker.loading || !picker.hasMore}
            className="inline-flex min-h-9 items-center rounded-sm border border-border bg-background px-3 text-sm hover:bg-muted disabled:opacity-50"
          >
            {picker.loading ? "Loading…" : "Load more"}
          </button>
          <p className="text-xs text-muted-foreground">
            Each page of {PICKER_PAGE_SIZE} asks YouTube again and takes a few seconds.
          </p>
        </div>
      ) : null}
    </section>
  );
}

const SKELETON_KEYS = ["sk-1", "sk-2", "sk-3", "sk-4"] as const;

function PickerRow({
  snippet,
  checked,
  showChannel,
  onToggle,
}: {
  snippet: VideoSnippet;
  checked: boolean;
  showChannel: boolean;
  onToggle: (id: string, on: boolean, shift: boolean) => void;
}): JSX.Element {
  const title = snippet.title ?? snippet.id;
  const duration = durationLabel(snippet);
  const meta = [
    snippet.view_count != null ? `${fmtCount(snippet.view_count)} views` : null,
    fmtDate(snippet.upload_date),
    showChannel ? snippet.channel : null,
  ]
    .filter(Boolean)
    .join(" · ");
  return (
    <li data-id={snippet.id} className={`${ROW_GRID} ${checked ? "bg-muted" : "hover:bg-muted/50"}`}>
      <label className="flex size-9 cursor-pointer items-center justify-center">
        <input
          type="checkbox"
          checked={checked}
          // The click is the one event that carries Shift (a change event does not), and Space on the box is a click too.
          readOnly
          onClick={(event) => onToggle(snippet.id, !checked, event.shiftKey)}
          aria-label={`Select ${title}`}
          className="size-4 cursor-pointer accent-primary"
        />
      </label>
      <span className="relative mt-0.5 block aspect-video w-24 shrink-0 overflow-hidden rounded-sm border border-border bg-muted">
        {snippet.thumbnail ? (
          // biome-ignore lint/performance/noImgElement: YouTube thumbnails come from changing CDN hosts
          <img src={snippet.thumbnail} alt="" loading="lazy" className="size-full object-cover" />
        ) : null}
        {duration ? (
          <span className="absolute right-1 bottom-1 rounded-sm bg-foreground px-1 py-0.5 font-mono text-xs leading-none text-background">
            {duration}
          </span>
        ) : null}
      </span>
      <div className="min-w-0 pt-0.5">
        <p className="line-clamp-2 text-sm leading-snug font-medium">{title}</p>
        {meta ? <p className="mt-0.5 font-mono text-xs text-muted-foreground">{meta}</p> : null}
      </div>
    </li>
  );
}

function PickerSkeleton(): JSX.Element {
  return (
    <li aria-hidden className={`${ROW_GRID} animate-status`}>
      <span className="size-9" />
      <span className="aspect-video w-24 rounded-sm bg-muted" />
      <span className="flex flex-col gap-2 pt-1.5">
        <span className="h-4 w-3/5 rounded-sm bg-muted" />
        <span className="h-3 w-2/5 rounded-sm bg-muted" />
      </span>
    </li>
  );
}
