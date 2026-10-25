"use client";

/** Ingestion-stage library explorer: grouped tree of ingested pages with
 * search, tier chips and a plain-text preview pane. Detection of docs
 * lives only in state via zod-parsed manifest data. Keys: "/" focuses
 * search, Esc clears; ArrowUp/Down move selection. Port of explorer.js. */

import { useMemo, useRef, useState } from "react";

import { Markdown } from "@/shared/markdown";
import { Button } from "@/shared/ui/button";

import { fmtBytes } from "../domain/format";
import { explorerTierValues, filterEntries, groupEntries, ROOT_GROUP } from "../domain/ingestion";
import type { Manifest } from "../entities";
import { usePageBody } from "../hooks/use-dd-data";
import { FileDrawer } from "./file-drawer";

const PREVIEW_MODES = [
  { id: "rendered", label: "Rendered" },
  { id: "source", label: "Source" },
] as const;

export function Explorer({ slug, manifest }: { slug: string; manifest: Manifest | null }) {
  const [previewMode, setPreviewMode] = useState<(typeof PREVIEW_MODES)[number]["id"]>("rendered");
  const [query, setQuery] = useState("");
  const [tiers, setTiers] = useState<ReadonlySet<string>>(new Set());
  const [selectedIdx, setSelectedIdx] = useState<number | null>(null);
  const [drawerPosition, setDrawerPosition] = useState<number | null>(null);
  const searchRef = useRef<HTMLInputElement | null>(null);

  const entries = useMemo(() => manifest?.entries ?? [], [manifest]);
  const filtered = useMemo(() => {
    return filterEntries(entries, tiers, query)
      .map((index) => entries[index])
      .filter((entry) => entry != null);
  }, [entries, tiers, query]);
  const groups = useMemo(() => groupEntries(filtered), [filtered]);
  const selected = useMemo(() => {
    if (selectedIdx != null && entries.some((entry) => entry.idx === selectedIdx)) {
      return selectedIdx;
    }
    return filtered[0]?.idx ?? null;
  }, [entries, filtered, selectedIdx]);

  const page = usePageBody(slug, selected);

  const onRootKeyDown = (event: React.KeyboardEvent) => {
    if (event.key === "/" && document.activeElement !== searchRef.current) {
      event.preventDefault();
      searchRef.current?.focus();
      return;
    }
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      if (filtered.length === 0) {
        return;
      }
      const position = filtered.findIndex((entry) => entry.idx === selected);
      const next =
        event.key === "ArrowDown" ? Math.min(position + 1, filtered.length - 1) : Math.max(position - 1, 0);
      const target = filtered[next]?.idx;
      if (target != null) {
        setSelectedIdx(target);
      }
    }
  };

  const tierValues = explorerTierValues(entries);

  return (
    <section
      aria-label="Ingested library"
      onKeyDown={onRootKeyDown}
      className="grid grid-cols-1 gap-4 lg:grid-cols-5"
    >
      <div className="lg:col-span-2">
        <div className="mb-2 flex flex-wrap items-center gap-2">
          <input
            ref={searchRef}
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={(e) => e.key === "Escape" && setQuery("")}
            placeholder="Search pages  ( / )"
            aria-label="Search pages"
            className="h-9 min-w-48 flex-1 rounded-sm border border-border bg-background px-2 text-sm"
          />
          {tierValues.map((t) => {
            const active = tiers.has(t);
            return (
              <button
                key={t}
                type="button"
                aria-pressed={active}
                onClick={() =>
                  setTiers((current) => {
                    const next = new Set(current);
                    if (active) {
                      next.delete(t);
                    } else {
                      next.add(t);
                    }
                    return next;
                  })
                }
                className={`min-h-9 rounded-sm border px-2 text-xs ${
                  active
                    ? "border-primary bg-primary/10 text-primary-text"
                    : "border-border text-muted-foreground hover:text-foreground"
                }`}
              >
                {t}
              </button>
            );
          })}
        </div>
        <div className="max-h-[60vh] overflow-auto rounded-md border border-border bg-card">
          {groups.map((group) => (
            <div key={group.prefix}>
              <p className="border-b border-border bg-muted px-2 py-1 text-xs font-medium text-muted-foreground">
                {group.prefix === ROOT_GROUP ? "(root)" : group.prefix}
              </p>
              <ul>
                {group.items.map(({ entry }) => (
                  <li key={entry.idx}>
                    <button
                      type="button"
                      onClick={() => setSelectedIdx(entry.idx)}
                      aria-current={selected === entry.idx || undefined}
                      className={`flex w-full min-h-9 items-baseline gap-2 px-2 py-1 text-left text-xs hover:bg-muted ${
                        selected === entry.idx ? "bg-primary/5" : ""
                      }`}
                    >
                      <span className="truncate text-foreground">{entry.title || entry.slug}</span>
                      <span aria-hidden className="ml-auto shrink-0 text-muted-foreground">
                        {entry.tier ? ` · ${entry.tier}` : ""}
                        {" · "}
                        {fmtBytes(entry.bytes ?? 0)}
                      </span>
                    </button>{" "}
                  </li>
                ))}
              </ul>
            </div>
          ))}
          {groups.length === 0 ? (
            <p className="p-4 text-center text-xs text-muted-foreground">
              {entries.length === 0
                ? manifestQueryPendingHint(manifest)
                : "No pages match the current filters."}
            </p>
          ) : null}
        </div>
        <p aria-live="polite" className="mt-2 text-xs text-muted-foreground">
          {filtered.length} of {entries.length} pages
        </p>
      </div>
      <div className="lg:col-span-3">
        <div className="rounded-md border border-border bg-card">
          <div className="flex flex-wrap items-center gap-2 border-b border-border px-3 py-2 text-xs text-muted-foreground">
            <fieldset className="m-0 flex min-w-0 border-0 p-0">
              <legend className="sr-only">Preview as</legend>
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
            <span className="min-w-0 flex-1 break-words">{pagePreviewTitle(entries, selected)}</span>
            <Button
              variant="outline"
              size="lg"
              disabled={selected === null}
              onClick={() => setDrawerPosition(entries.findIndex((entry) => entry.idx === selected))}
              aria-haspopup="dialog"
            >
              Open full page
            </Button>
          </div>
          <div className="min-h-48 p-4">
            {page.isPending && selected != null ? (
              <p className="text-xs text-muted-foreground">Loading page…</p>
            ) : page.isError ? (
              <p role="alert" className="text-xs text-destructive-text">
                Page unavailable.
              </p>
            ) : page.data == null ? (
              <p className="text-xs text-muted-foreground">Select a page from the list.</p>
            ) : (
              <section
                aria-label={previewMode === "rendered" ? "Rendered page" : "Page source"}
                // biome-ignore lint/a11y/noNoninteractiveTabindex: a scrollable region must be reachable by keyboard (WCAG 2.1.1)
                tabIndex={0}
                className="max-h-[60vh] overflow-auto pr-1"
              >
                {previewMode === "rendered" ? (
                  <Markdown text={page.data} allowHtml />
                ) : (
                  <pre className="font-mono text-xs whitespace-pre-wrap text-foreground">{page.data}</pre>
                )}
              </section>
            )}
          </div>
        </div>
      </div>
      {drawerPosition !== null && drawerPosition >= 0 ? (
        <FileDrawer
          slug={slug}
          entries={entries}
          position={drawerPosition}
          onMove={setDrawerPosition}
          onClose={() => setDrawerPosition(null)}
        />
      ) : null}
    </section>
  );
}

function pagePreviewTitle(entries: Manifest["entries"], selected: number | null): string {
  if (selected == null) {
    return "—";
  }
  const entry = entries.find((e) => e.idx === selected);
  return entry?.title || entry?.slug || String(selected);
}

function manifestQueryPendingHint(manifest: Manifest | null): string {
  return manifest ? "No pages match the current filters." : "No ingested pages yet.";
}
