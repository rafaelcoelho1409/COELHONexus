"use client";

import type { JSX } from "react";

import { durationLabel, fmtCount, fmtDate } from "../domain";
import type { VideoSnippet } from "../entities";
import type { Picker } from "../hooks/use-picker";

/** The picker's master+row list: source identity + counts on top, tri-state master checkbox + title
 * filter, one row per video, Load more + selection count at the bottom. The submit and ingest-all
 * actions stay in the tab's sticky bar, next to the options they apply to. */
export function PickerView({ picker }: { picker: Picker }): JSX.Element {
  const visible = picker.filtered;
  const master =
    visible.length === 0 ? "none" : visible.every((v) => picker.selected.has(v.id)) ? "all" : "some";
  const totalText =
    picker.total != null ? `${fmtCount(picker.total)} videos` : `${picker.items.length} loaded · total ?`;
  const loadedText =
    picker.total != null
      ? `${picker.items.length} of ${fmtCount(picker.total)} loaded`
      : `${picker.items.length} loaded`;

  return (
    <div className="rounded-md border border-border bg-card">
      <div className="flex flex-wrap items-end justify-between gap-x-4 gap-y-2 border-b border-border p-3">
        <div className="min-w-0">
          <div className="truncate text-sm font-medium">{picker.title ?? ""}</div>
          <div className="truncate text-xs text-muted-foreground">{picker.channel ?? ""}</div>
        </div>
        <div className="text-xs text-muted-foreground">
          <span className="font-mono">{totalText}</span>
          <span className="ml-3 font-mono">{loadedText}</span>
        </div>
      </div>
      <div className="flex flex-wrap items-center gap-x-4 gap-y-2 border-b border-border px-3 py-2">
        <label className="flex min-h-9 items-center gap-2 text-sm">
          <input
            type="checkbox"
            ref={(el) => {
              if (el) el.indeterminate = master === "some";
            }}
            checked={master === "all"}
            onChange={(e) =>
              picker.setAll(
                visible.map((v) => v.id),
                e.currentTarget.checked,
              )
            }
            aria-label="Select all loaded videos"
          />
          Select all loaded
        </label>
        <input
          type="search"
          value={picker.filterQ}
          onChange={(e) => picker.setFilterQ(e.currentTarget.value)}
          placeholder="Filter by title or channel…"
          aria-label="Filter loaded videos"
          autoComplete="off"
          className="h-8 w-full max-w-xs rounded-sm border border-border bg-background px-2 text-sm sm:w-64"
        />
      </div>
      <ul aria-label="Source videos" className="max-h-[28rem] overflow-auto">
        {visible.map((v) => (
          <PickerRow key={v.id} snippet={v} checked={picker.selected.has(v.id)} onToggle={picker.toggle} />
        ))}
        {visible.length === 0 ? (
          <li className="px-3 py-3 text-sm text-muted-foreground">
            {picker.items.length === 0
              ? picker.error
                ? picker.error
                : "Nothing loaded yet."
              : "No matches — clear the filter to see all."}
          </li>
        ) : null}
      </ul>
      <div className="flex items-center justify-between gap-x-4 border-t border-border px-3 py-2">
        {picker.hasMore ? (
          <button
            type="button"
            onClick={() => void picker.loadMore()}
            disabled={picker.loading}
            className="inline-flex h-9 items-center rounded-sm border border-border bg-card px-3 text-sm hover:bg-muted disabled:opacity-50"
          >
            {picker.loading ? "Loading…" : "Load more"}
          </button>
        ) : (
          <span />
        )}
        <div className="text-sm text-muted-foreground">
          <span className="font-mono text-foreground">{picker.selected.size}</span> selected
        </div>
      </div>
    </div>
  );
}

function PickerRow({
  snippet,
  checked,
  onToggle,
}: {
  snippet: VideoSnippet;
  checked: boolean;
  onToggle: (id: string, on: boolean) => void;
}): JSX.Element {
  const meta = [
    snippet.view_count != null ? `${fmtCount(snippet.view_count)} views` : null,
    fmtDate(snippet.upload_date),
  ]
    .filter(Boolean)
    .join(" · ");
  return (
    <li className="flex items-center gap-3 border-b border-border px-3 py-2 last:border-b-0">
      <input
        type="checkbox"
        checked={checked}
        onChange={(e) => onToggle(snippet.id, e.currentTarget.checked)}
        aria-label={`Select ${snippet.title ?? snippet.id}`}
      />
      {snippet.thumbnail ? (
        // biome-ignore lint/performance/noImgElement: YouTube thumbnails of arbitrary hosts — next/image would need every host allow-listed
        <img // eslint-disable-line @next/next/no-img-element
          src={snippet.thumbnail}
          alt=""
          loading="lazy"
          className="h-9 w-16 shrink-0 rounded-sm object-cover"
        />
      ) : (
        <div aria-hidden className="h-9 w-16 shrink-0 rounded-sm bg-muted" />
      )}
      <div className="min-w-0 flex-1">
        <div className="truncate text-sm">{snippet.title ?? snippet.id}</div>
        {meta ? <div className="text-xs text-muted-foreground">{meta}</div> : null}
      </div>
      <span className="shrink-0 font-mono text-xs text-muted-foreground">{durationLabel(snippet) ?? ""}</span>
    </li>
  );
}
