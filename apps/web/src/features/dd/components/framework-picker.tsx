"use client";

/** The framework picker ("name ▾") shared by every non-catalog stage:
 * search + ingested library, per-item refresh/delete actions with the
 * delete confirmation, and in-place navigation on select. Port of
 * picker.py + catalog/picker.js + library.js. */

import { useQueryClient } from "@tanstack/react-query";
import { useRouter } from "next/navigation";
import { useEffect, useMemo, useRef, useState } from "react";

import { getDdIngestionQueryKey } from "@/shared/api";
import { apiErrorMessage } from "@/shared/lib/api-error";

import { fmtAge } from "../domain/format";
import { deleteCascadeText, itemDisplayName, itemLogos } from "../domain/library";
import type { LibraryItem } from "../entities";
import { useDeleteLibrary, useLibrary, useStartRun } from "../hooks/use-dd-data";
import { useDdOverlays } from "../hooks/use-overlays";
import { type DdStage, ingestionHref, stageHref } from "../params";

export function FrameworkPicker({ stage, currentSlug }: { stage: DdStage; currentSlug: string | null }) {
  const libraryQuery = useLibrary();
  const items: LibraryItem[] = useMemo(() => libraryQuery.data ?? [], [libraryQuery.data]);
  const router = useRouter();
  const { confirm, toast, notice } = useDdOverlays();
  const start = useStartRun();
  const del = useDeleteLibrary();
  const queryClient = useQueryClient();

  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const boxRef = useRef<HTMLDivElement | null>(null);
  const searchRef = useRef<HTMLInputElement | null>(null);

  useEffect(() => {
    if (!open) {
      return;
    }
    const onDocClick = (event: MouseEvent) => {
      if (boxRef.current && event.target instanceof Node && !boxRef.current.contains(event.target)) {
        setOpen(false);
      }
    };
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setOpen(false);
      }
    };
    document.addEventListener("mousedown", onDocClick);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDocClick);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  useEffect(() => {
    if (open) {
      searchRef.current?.focus();
    }
  }, [open]);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) {
      return items;
    }
    return items.filter((item) => itemDisplayName(item).toLowerCase().includes(q));
  }, [items, query]);

  const busy = start.isPending || del.isPending;
  const currentItem = currentSlug ? (items.find((item) => item.slug === currentSlug) ?? null) : null;

  const pick = (slug: string) => {
    setOpen(false);
    setQuery("");
    if (slug === currentSlug) {
      return;
    }
    router.push(stageHref(stage, slug));
  };

  const refresh = (slug: string) => {
    start.mutate(
      { slug, refresh: true },
      {
        onSuccess: (outcome) => {
          if (outcome.kind === "queued") {
            notice(`Reingesting ${nameForSlug(items, slug)}…`);
            void queryClient.invalidateQueries({ queryKey: getDdIngestionQueryKey() });
            router.push(ingestionHref(slug, outcome.runId ?? null));
          } else if (outcome.kind === "locked") {
            toast(outcome.message ?? "A run is already active for this framework.");
          } else {
            notice("Already up to date.");
          }
        },
        onError: (error) => toast(apiErrorMessage(error)),
      },
    );
  };

  const remove = (slug: string) => {
    void confirm({
      title: "Remove from library?",
      message: `This deletes ${nameForSlug(items, slug)}'s ingested data permanently.${deleteCascadeText({ planner: true, synth: true }, "")}`,
      confirmLabel: "Delete",
    }).then((yes) => {
      if (!yes) {
        return;
      }
      del.mutate(slug, {
        onSuccess: () => {
          notice(`Removed ${nameForSlug(items, slug)} from the library.`);
          if (slug === currentSlug) {
            router.push(stageHref(stage));
          }
        },
        onError: (error) => toast(apiErrorMessage(error)),
      });
    });
  };

  return (
    <div className="relative" ref={boxRef}>
      <button
        type="button"
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
        className="min-h-9 rounded-sm border border-border bg-card px-3 py-1.5 text-sm text-foreground hover:border-foreground/40"
      >
        {currentItem ? (
          <span className="flex items-center gap-2">
            <span className="max-w-56 truncate font-medium">{itemDisplayName(currentItem)}</span>
            <span aria-hidden className="text-muted-foreground">
              ▾
            </span>
          </span>
        ) : (
          <span className="flex items-center gap-2">
            Select framework
            <span aria-hidden className="text-muted-foreground">
              ▾
            </span>
          </span>
        )}
      </button>
      {open ? (
        <div className="absolute left-0 z-40 mt-1 w-80 rounded-md border border-border bg-card p-2">
          <input
            ref={searchRef}
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search frameworks…"
            aria-label="Search frameworks"
            className="mb-1 h-9 w-full rounded-sm border border-border bg-background px-2 text-sm"
          />
          <ul aria-label="Ingested frameworks" className="max-h-64 overflow-auto">
            {filtered.map((item) => {
              const logos = itemLogos(item);
              return (
                <li key={item.slug} className="group flex items-center gap-1 rounded-sm px-1 hover:bg-muted">
                  <button
                    type="button"
                    onClick={() => pick(item.slug)}
                    className="flex min-h-9 flex-1 items-center gap-2 truncate text-left text-sm"
                  >
                    {logos.length > 0 ? (
                      <span aria-hidden className="shrink-0 text-xs text-muted-foreground">
                        {logos.slice(0, 3).join("·")}
                      </span>
                    ) : null}
                    <span className="truncate font-medium">{itemDisplayName(item)}</span>
                    <span className="ml-auto shrink-0 text-xs text-muted-foreground">{itemMeta(item)}</span>
                  </button>
                  <button
                    type="button"
                    aria-label={`Refresh ${itemDisplayName(item)}`}
                    disabled={busy}
                    onClick={() => refresh(item.slug)}
                    title="Re-ingest (fresh crawls)"
                    className="min-h-9 px-1 text-muted-foreground hover:text-foreground disabled:opacity-50"
                  >
                    ↻
                  </button>
                  <button
                    type="button"
                    aria-label={`Delete ${itemDisplayName(item)}`}
                    disabled={busy}
                    onClick={() => remove(item.slug)}
                    title="Delete"
                    className="min-h-9 px-1 text-muted-foreground hover:text-destructive-text disabled:opacity-50"
                  >
                    🗑
                  </button>
                </li>
              );
            })}
            {filtered.length === 0 ? (
              <li className="px-2 py-3 text-sm text-muted-foreground">
                {items.length > 0
                  ? query
                    ? "No match."
                    : "No frameworks selected."
                  : libraryQuery.isPending
                    ? "Loading library…"
                    : "Ingest something first."}
              </li>
            ) : null}
          </ul>
        </div>
      ) : null}
    </div>
  );
}

function nameForSlug(items: LibraryItem[], slug: string): string {
  const found = items.find((item) => item.slug === slug);
  return found ? itemDisplayName(found) : slug;
}

function itemMeta(item: Pick<LibraryItem, "page_count" | "ingested_at">): string {
  const pages = typeof item.page_count === "number" && item.page_count > 0 ? `${item.page_count} pages` : "";
  const age = fmtAge(item.ingested_at ?? null);
  return [pages, age].filter(Boolean).join(" · ");
}
