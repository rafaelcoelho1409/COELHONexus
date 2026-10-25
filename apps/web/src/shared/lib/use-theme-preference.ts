"use client";

import { useSyncExternalStore } from "react";

import { isThemePreference, resolveTheme, THEME_STORAGE_KEY, type ThemePreference } from "./theme";

const DARK_QUERY = "(prefers-color-scheme: dark)";
const listeners = new Set<() => void>();

function notify() {
  for (const listener of listeners) listener();
}

function subscribe(listener: () => void) {
  listeners.add(listener);
  const media = window.matchMedia(DARK_QUERY);
  window.addEventListener("storage", listener);
  media.addEventListener("change", listener);
  return () => {
    listeners.delete(listener);
    window.removeEventListener("storage", listener);
    media.removeEventListener("change", listener);
  };
}

function read(): ThemePreference {
  try {
    const stored = window.localStorage.getItem(THEME_STORAGE_KEY);
    return isThemePreference(stored) ? stored : "system";
  } catch {
    return "system"; // storage blocked (private mode, sandboxed iframe)
  }
}

/** Applies the preference to the document, then tells every subscriber. */
function setThemePreference(preference: ThemePreference) {
  try {
    window.localStorage.setItem(THEME_STORAGE_KEY, preference);
  } catch {
    // the choice still applies for this page view
  }
  const systemPrefersDark = window.matchMedia(DARK_QUERY).matches;
  document.documentElement.setAttribute("data-theme", resolveTheme(preference, systemPrefersDark));
  notify();
}

/** The stored preference, safe for SSR: the server snapshot is "system", the client reads storage. */
export function useThemePreference() {
  const preference = useSyncExternalStore(subscribe, read, () => "system" as const);
  return [preference, setThemePreference] as const;
}
