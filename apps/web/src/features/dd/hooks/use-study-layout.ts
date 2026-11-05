"use client";

import { useSyncExternalStore } from "react";

import { parseStudyLayout, storeStudyLayout } from "../domain/study-layout";
import type { StudyLayout } from "../entities";
import { MATERIALS_LAYOUT_STORAGE_KEY, STUDY_LAYOUT_STORAGE_KEY } from "../params";

// A layout lives in localStorage under its own key; this store keeps every reader of it (and other tabs) in sync.
// `refused` holds the choice for a browser that will not take it (storage blocked or full), so a toggle still works
// for the rest of the page view.
const listeners = new Set<() => void>();
const refused = new Map<string, string>();

function subscribe(listener: () => void) {
  listeners.add(listener);
  window.addEventListener("storage", listener);
  return () => {
    listeners.delete(listener);
    window.removeEventListener("storage", listener);
  };
}

function read(key: string): string | null {
  const kept = refused.get(key);
  if (kept !== undefined) return kept;
  try {
    return window.localStorage.getItem(key);
  } catch {
    return null; // storage blocked: the default layout
  }
}

function write(key: string, value: string) {
  try {
    window.localStorage.setItem(key, value);
  } catch {
    refused.set(key, value); // not remembered across visits, still applies to this page view
  }
  for (const listener of listeners) listener();
}

/** The reader's layout preference stored under `key`, and a way to change part of it. Server render and first paint use the default. */
function useStoredLayout(key: string): {
  layout: StudyLayout;
  update: (patch: Partial<StudyLayout>) => void;
} {
  const raw = useSyncExternalStore(
    subscribe,
    () => read(key),
    () => null,
  );
  return {
    layout: parseStudyLayout(raw),
    update: (patch) => write(key, storeStudyLayout(parseStudyLayout(read(key)), patch)),
  };
}

/** The Study tab's layout. */
export function useStudyLayout() {
  return useStoredLayout(STUDY_LAYOUT_STORAGE_KEY);
}

/** The Ingestion tab's Materials reader: the same layout, with the page list as its left column. */
export function useMaterialsLayout() {
  return useStoredLayout(MATERIALS_LAYOUT_STORAGE_KEY);
}
