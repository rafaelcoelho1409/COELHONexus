"use client";

import { useSyncExternalStore } from "react";

import { AUTO_CHAIN_STORAGE_KEY } from "../params";

const listeners = new Set<() => void>();
let fallbackValue = false;

function getSnapshot(): boolean {
  try {
    return window.localStorage.getItem(AUTO_CHAIN_STORAGE_KEY) === "true";
  } catch {
    return fallbackValue;
  }
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  window.addEventListener("storage", listener);
  return () => {
    listeners.delete(listener);
    window.removeEventListener("storage", listener);
  };
}

function setPreference(enabled: boolean): void {
  fallbackValue = enabled;
  try {
    window.localStorage.setItem(AUTO_CHAIN_STORAGE_KEY, String(enabled));
  } catch {
    fallbackValue = enabled;
  }
  for (const listener of listeners) listener();
}

export function useAutoChainPreference(): readonly [boolean, (enabled: boolean) => void] {
  const enabled = useSyncExternalStore(subscribe, getSnapshot, () => false);
  return [enabled, setPreference];
}
