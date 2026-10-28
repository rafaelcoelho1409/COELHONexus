"use client";

/** A per-browser preference held in localStorage (theme posture: never anything the server knows).
 * `useSyncExternalStore` reads the real value after hydration and the fallback on the server, so no
 * effect ever needs to sync state. Writes notify the subscribers (the `storage` event only crosses tabs). */

import { useSyncExternalStore } from "react";

const listeners = new Set<() => void>();

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  window.addEventListener("storage", listener);
  return () => {
    listeners.delete(listener);
    window.removeEventListener("storage", listener);
  };
}

export function useBrowserPref(key: string, fallback: string): [string, (value: string) => void] {
  const value = useSyncExternalStore(
    subscribe,
    () => {
      try {
        return window.localStorage.getItem(key) ?? fallback;
      } catch {
        return fallback; // storage blocked: the preference simply isn't remembered
      }
    },
    () => fallback,
  );
  const setValue = (next: string) => {
    try {
      window.localStorage.setItem(key, next);
    } catch {
      // not remembered across reloads, still applies to this page view
    }
    for (const listener of listeners) listener();
  };
  return [value, setValue];
}
