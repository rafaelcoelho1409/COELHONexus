"use client";

/** State for the shared source picker — one channel/playlist/batch's videos, accumulated page by page,
 * with tri-state selection (Shift-click selects a range), a client-side title filter and Load more. The DOM
 * work lives in `picker-view.tsx`. */

import { useState } from "react";

import { apiErrorMessage } from "@/shared/lib/api-error";

import { fmtCount } from "../domain";
import type { Enumeration, VideoSnippet } from "../entities";
import { PICKER_PAGE_SIZE } from "../params";

export type PickerFetch = (offset: number, limit: number) => Promise<Enumeration>;

export function usePicker(fetchPage: PickerFetch) {
  const [items, setItems] = useState<VideoSnippet[]>([]);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [lastToggled, setLastToggled] = useState<string | null>(null);
  const [title, setTitle] = useState<string | null>(null);
  const [channel, setChannel] = useState<string | null>(null);
  const [total, setTotal] = useState<number | null>(null);
  const [hasMore, setHasMore] = useState(false);
  const [filterQ, setFilterQ] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const appendPage = async (offset: number, replace: boolean): Promise<void> => {
    setLoading(true);
    setError(null);
    try {
      const page = await fetchPage(offset, PICKER_PAGE_SIZE);
      const incoming = page.items;
      if (replace) {
        setItems(incoming);
        setTotal(page.total ?? null);
        setTitle(page.title ?? null);
        setChannel(page.channel ?? null);
      } else {
        setItems((prev) => [...prev, ...incoming]);
        setTotal((prev) => prev ?? page.total ?? null);
      }
      setHasMore(page.has_more);
    } catch (e) {
      setError(apiErrorMessage(e));
      throw e;
    } finally {
      setLoading(false);
    }
  };

  const refetch = async (): Promise<void> => {
    setItems([]);
    setSelected(new Set());
    setLastToggled(null);
    setTotal(null);
    setTitle(null);
    setChannel(null);
    setHasMore(false);
    setFilterQ("");
    return appendPage(0, true);
  };

  const loadMore = async (): Promise<void> => {
    if (loading || !hasMore) return;
    return appendPage(items.length, false);
  };

  const query = filterQ.trim().toLowerCase();
  const filtered = query
    ? items.filter(
        (v) =>
          (v.title ?? "").toLowerCase().includes(query) || (v.channel ?? "").toLowerCase().includes(query),
      )
    : items;

  const setAll = (ids: ReadonlyArray<string>, on: boolean): void => {
    setSelected((prev) => {
      const next = new Set(prev);
      for (const id of ids) {
        if (on) next.add(id);
        else next.delete(id);
      }
      return next;
    });
  };

  /** Tick or untick one row; with `shift`, every row between it and the last one touched, in the order shown. */
  const toggle = (id: string, on: boolean, shift = false): void => {
    if (shift && lastToggled) {
      const from = filtered.findIndex((v) => v.id === lastToggled);
      const to = filtered.findIndex((v) => v.id === id);
      if (from !== -1 && to !== -1) {
        const [lo, hi] = from < to ? [from, to] : [to, from];
        setAll(
          filtered.slice(lo, hi + 1).map((v) => v.id),
          on,
        );
        setLastToggled(id);
        return;
      }
    }
    setAll([id], on);
    setLastToggled(id);
  };

  const clearSelection = (): void => {
    setSelected(new Set());
    setLastToggled(null);
  };

  return {
    items,
    filtered,
    selected,
    title,
    channel,
    total,
    hasMore,
    filterQ,
    setFilterQ,
    loading,
    error,
    refetch,
    loadMore,
    toggle,
    setAll,
    clearSelection,
  };
}

export type Picker = ReturnType<typeof usePicker>;

/** The status line under the input — loading, or the loaded count. */
export function pickerStatus(picker: Picker): string | null {
  if (picker.loading) return "Loading videos…";
  if (picker.items.length === 0) return null;
  return picker.total != null
    ? `Loaded ${picker.items.length} of ${fmtCount(picker.total)} videos.`
    : `Loaded ${picker.items.length} videos.`;
}
