"use client";

import { useSyncExternalStore } from "react";

import { parseStudyLayout, storeStudyLayout } from "../domain/study-layout";
import type { StudyLayout } from "../entities";
import { STUDY_LAYOUT_STORAGE_KEY } from "../params";

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
    return window.localStorage.getItem(STUDY_LAYOUT_STORAGE_KEY);
  } catch {
    return null; // storage blocked: the default layout
  }
}

function write(value: string) {
  try {
    window.localStorage.setItem(STUDY_LAYOUT_STORAGE_KEY, value);
  } catch {
    refused = value; // not remembered across visits, still applies to this page view
  }
  for (const listener of listeners) listener();
}

/** The reader's layout preference and a way to change part of it. Server render and first paint use the default. */
export function useStudyLayout(): { layout: StudyLayout; update: (patch: Partial<StudyLayout>) => void } {
  const raw = useSyncExternalStore(subscribe, read, () => null);
  return {
    layout: parseStudyLayout(raw),
    update: (patch) => write(storeStudyLayout(parseStudyLayout(read()), patch)),
  };
}
