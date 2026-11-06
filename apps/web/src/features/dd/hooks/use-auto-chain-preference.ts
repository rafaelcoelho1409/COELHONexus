"use client";

import { useSyncExternalStore } from "react";

import { AUTO_CHAIN_STORAGE_KEY } from "../params";

// The opt-in lives in localStorage (same key the old UI used); the store keeps every reader in sync, so the toggle in the
// third navbar and the pipeline that obeys it never disagree.
const listeners = new Set<() => void>();

function subscribe(listener: () => void) {
  listeners.add(listener);
  window.addEventListener("storage", listener);
  return () => {
    listeners.delete(listener);
    window.removeEventListener("storage", listener);
  };
}

function readEnabled(): boolean {
  try {
    return window.localStorage.getItem(AUTO_CHAIN_STORAGE_KEY) === "1";
  } catch {
    return false; // storage blocked: the choice simply isn't remembered
  }
}

function writeEnabled(enabled: boolean) {
  try {
    window.localStorage.setItem(AUTO_CHAIN_STORAGE_KEY, enabled ? "1" : "0");
  } catch {
    // not remembered across reloads, still applies to this page view
  }
  for (const listener of listeners) listener();
}

export function useAutoChainPreference(): readonly [boolean, (enabled: boolean) => void] {
  const enabled = useSyncExternalStore(subscribe, readEnabled, () => false);
  return [enabled, writeEnabled];
}
