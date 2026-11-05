"use client";

import { useSyncExternalStore } from "react";

import { parseReaderLayout, storeReaderLayout } from "../domain/reader-layout";
import type { ReaderLayout } from "../entities";
import { READER_LAYOUT_STORAGE_KEY } from "../params";

// The layout lives in localStorage; this store keeps every reader of it (and other tabs) in sync. `refused` holds the
// choice for a browser that will not take it (storage blocked or full), so a toggle still works for the rest of the page view.
const listeners = new Set<() => void>();
let refused: string | undefined;

function subscribe(listener: () => void) {
  listeners.add(listener);
  window.addEventListener("storage", listener);
  return () => {
    listeners.delete(listener);
    window.removeEventListener("storage", listener);
  };
}

function read(): string | null {
  if (refused !== undefined) return refused;
  try {
    return window.localStorage.getItem(READER_LAYOUT_STORAGE_KEY);
  } catch {
    return null; // storage blocked: the default layout
  }
}

function write(value: string) {
  try {
    window.localStorage.setItem(READER_LAYOUT_STORAGE_KEY, value);
  } catch {
    refused = value; // not remembered across visits, still applies to this page view
  }
  for (const listener of listeners) listener();
}

/** The reader's layout preference and a way to change part of it. Server render and first paint use the default. */
export function useReaderLayout(): { layout: ReaderLayout; update: (patch: Partial<ReaderLayout>) => void } {
  const raw = useSyncExternalStore(subscribe, read, () => null);
  return {
    layout: parseReaderLayout(raw),
    update: (patch) => write(storeReaderLayout(parseReaderLayout(read()), patch)),
  };
}
