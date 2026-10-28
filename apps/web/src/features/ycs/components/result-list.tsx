"use client";

import { Check, Copy, ExternalLink, ListVideo, Play, User, Video } from "lucide-react";
import { type JSX, useState } from "react";

import { durationLabel, fmtCount, resultMeta } from "../domain";
import type { VideoSnippet } from "../entities";

export type Density = "compact" | "comfortable";

/** The master row at the top of the result list — tri-state per Gmail/Linear: unchecked, indeterminate
 * when some rows are selected, checked when every visible row is. */
export function MasterRow({
  state,
  onToggle,
  totalVisible,
  selectedCount,
}: {
  state: "all" | "some" | "none";
  onToggle: () => void;
  totalVisible?: number;
  selectedCount?: number;
}): JSX.Element {
  return (
    <li className="flex items-center justify-between border-b border-border bg-muted/40 px-3.5 py-2 text-xs font-medium text-muted-foreground select-none">
      <label className="flex items-center gap-2.5 cursor-pointer">
        <input
          type="checkbox"
          ref={(el) => {
            if (el) el.indeterminate = state === "some";
          }}
          checked={state === "all"}
          onChange={onToggle}
          aria-label="Select all visible results"
          className="size-4 rounded border-border text-primary focus:ring-primary/40"
        />
        <span className="text-sm font-medium text-foreground">Select all</span>
        {totalVisible != null ? (
          <span className="text-xs text-muted-foreground">({totalVisible} on page)</span>
        ) : null}
      </label>
      {selectedCount && selectedCount > 0 ? (
        <span className="text-xs text-muted-foreground">
          <strong className="text-foreground">{selectedCount}</strong> selected
        </span>
      ) : null}
    </li>
  );
}

/** One result row — checkbox, thumbnail link with duration overlay, kind badge, title link,
 * per-kind meta line, description, and an interactive copy-id control. `data-id` lets the
 * Shift-range finder locate rows. */
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
  const kind = snippet.kind || "video";
  const duration = durationLabel(snippet);

  const handleCopy = () => {
    onCopyId();
    setCopied(true);
    setTimeout(() => setCopied(false), 1800);
  };

  const kindBadgeClass =
    kind === "channel"
      ? "bg-purple-500/10 text-purple-700 dark:text-purple-300 border-purple-500/20"
      : kind === "playlist"
        ? "bg-emerald-500/10 text-emerald-700 dark:text-emerald-300 border-emerald-500/20"
        : "bg-primary/10 text-primary-text border-primary/20";

  return (
    <li
      data-id={snippet.id}
      className={`group flex items-start gap-3.5 border-b border-border px-3.5 transition-colors hover:bg-muted/30 ${
        checked ? "bg-muted/40" : ""
      } ${density === "compact" ? "py-2.5" : "py-3.5"} last:border-b-0`}
    >
      <div className="pt-1">
        <input
          type="checkbox"
          checked={checked}
          onChange={(e) => {
            const shiftKey = (e.nativeEvent as MouseEvent).shiftKey ?? false;
            onCheck(e.target.checked, shiftKey);
          }}
          onClick={(e) => {
            // Support direct click with shift
            if (e.shiftKey) {
              onCheck(!checked, true);
            }
          }}
          aria-label={`Select ${snippet.title ?? snippet.id}`}
          className="size-4 rounded border-border text-primary focus:ring-primary/40 cursor-pointer"
        />
      </div>

      {/* Thumbnail with duration overlay & hover play cue */}
      <a
        href={snippet.url}
        target="_blank"
        rel="noopener noreferrer"
        className={`group/thumb relative shrink-0 overflow-hidden rounded-md border border-border/80 bg-muted shadow-xs ${
          density === "compact" ? "h-14 w-24" : "h-18 w-32"
        }`}
      >
        {snippet.thumbnail ? (
          // biome-ignore lint/performance/noImgElement: YouTube thumbnails from dynamic CDN hosts
          <img
            src={snippet.thumbnail}
            alt=""
            loading="lazy"
            className="h-full w-full object-cover transition-transform duration-300 group-hover/thumb:scale-105"
          />
        ) : (
          <div
            aria-hidden
            className="flex h-full w-full items-center justify-center bg-muted text-muted-foreground"
          >
            {kind === "channel" ? (
              <User className="size-6 opacity-60" />
            ) : kind === "playlist" ? (
              <ListVideo className="size-6 opacity-60" />
            ) : (
              <Video className="size-6 opacity-60" />
            )}
          </div>
        )}

        {/* Hover play overlay */}
        <div className="absolute inset-0 flex items-center justify-center bg-black/35 opacity-0 transition-opacity group-hover/thumb:opacity-100">
          <Play className="size-5 fill-white text-white drop-shadow" />
        </div>

        {/* Duration badge overlay */}
        {duration && kind === "video" ? (
          <span className="absolute bottom-1 right-1 rounded bg-black/85 px-1 py-0.5 font-mono text-[10px] font-medium leading-none text-white shadow-sm ring-1 ring-white/10">
            {duration}
          </span>
        ) : null}
      </a>

      {/* Metadata & Title */}
      <div className="min-w-0 flex-1 pt-0.5">
        <div className="flex flex-wrap items-center gap-1.5 leading-snug">
          <span
            className={`inline-flex items-center gap-1 rounded-sm border px-1.5 py-0.5 font-mono text-[10px] font-semibold uppercase tracking-wider ${kindBadgeClass}`}
          >
            {kind === "channel" ? (
              <User className="size-3" />
            ) : kind === "playlist" ? (
              <ListVideo className="size-3" />
            ) : (
              <Video className="size-3" />
            )}
            {kind}
          </span>

          <a
            href={snippet.url}
            target="_blank"
            rel="noopener noreferrer"
            className="inline-flex items-center gap-1 font-medium text-foreground hover:text-primary-text hover:underline text-sm"
          >
            <span className="line-clamp-1">{snippet.title ?? "(no title)"}</span>
            <ExternalLink className="size-3 shrink-0 opacity-0 transition-opacity group-hover:opacity-60" />
          </a>
        </div>

        <div className="mt-1 flex flex-wrap items-center gap-x-2 text-xs text-muted-foreground font-mono">
          <span>{resultMeta(snippet)}</span>
        </div>

        {snippet.description ? (
          <p
            className={`mt-1 text-xs text-muted-foreground ${
              density === "compact" ? "line-clamp-1" : "line-clamp-2"
            }`}
          >
            {snippet.description}
          </p>
        ) : null}
      </div>

      {/* Copy ID Button */}
      <button
        type="button"
        onClick={handleCopy}
        title={copied ? "Copied to clipboard!" : `Copy ID: ${snippet.id}`}
        aria-label={snippet.id}
        className={`inline-flex items-center gap-1 self-start rounded-md border border-border px-2 py-1 font-mono text-xs transition-colors ${
          copied
            ? "border-emerald-500/30 bg-emerald-500/10 text-emerald-600 dark:text-emerald-400"
            : "bg-muted/50 text-muted-foreground hover:bg-muted hover:text-foreground"
        }`}
      >
        {copied ? (
          <Check className="size-3 text-emerald-600 dark:text-emerald-400" aria-hidden />
        ) : (
          <Copy className="size-3 opacity-70" aria-hidden />
        )}
        <span>{snippet.id}</span>
      </button>
    </li>
  );
}

/** Skeleton loader placeholder rows for smooth zero-shift loading state */
export function ResultSkeleton({ density }: { density: Density }): JSX.Element {
  return (
    <li
      className={`flex items-start gap-3.5 border-b border-border px-3.5 animate-pulse ${
        density === "compact" ? "py-2.5" : "py-3.5"
      }`}
    >
      <div className="mt-1 size-4 rounded bg-muted" />
      <div className={`shrink-0 rounded-md bg-muted ${density === "compact" ? "h-14 w-24" : "h-18 w-32"}`} />
      <div className="min-w-0 flex-1 pt-1 space-y-2">
        <div className="flex items-center gap-2">
          <div className="h-4 w-12 rounded bg-muted" />
          <div className="h-4 w-3/5 rounded bg-muted" />
        </div>
        <div className="h-3 w-2/5 rounded bg-muted" />
        {density === "comfortable" ? <div className="h-3 w-4/5 rounded bg-muted" /> : null}
      </div>
      <div className="h-6 w-20 rounded bg-muted" />
    </li>
  );
}

export function kindCountLabel(count: number, label: string): string {
  return `→ ${fmtCount(count)} ${label}`;
}
