export const THEME_STORAGE_KEY = "nexus-theme";

export type ThemePreference = "system" | "light" | "dark";
export type ResolvedTheme = "light" | "dark";

const PREFERENCES: readonly ThemePreference[] = ["system", "light", "dark"];

export function isThemePreference(value: unknown): value is ThemePreference {
  return typeof value === "string" && (PREFERENCES as readonly string[]).includes(value);
}

export function resolveTheme(preference: ThemePreference, systemPrefersDark: boolean): ResolvedTheme {
  if (preference === "system") return systemPrefersDark ? "dark" : "light";
  return preference;
}

/** The toggle cycles system → light → dark → system. */
export function nextPreference(preference: ThemePreference): ThemePreference {
  const index = PREFERENCES.indexOf(preference);
  return PREFERENCES[(index + 1) % PREFERENCES.length] ?? "system";
}

/**
 * Runs inline in `<head>`, before first paint, so the page never flashes the wrong theme. It must stay
 * dependency-free and mirror `resolveTheme`. It also honours `prefers-contrast: more` with the
 * high-contrast token set.
 */
export const themeInitScript = `(function(){var root=document.documentElement;try{var stored=localStorage.getItem(${JSON.stringify(THEME_STORAGE_KEY)});var preference=stored==="light"||stored==="dark"?stored:"system";var dark=window.matchMedia("(prefers-color-scheme: dark)").matches;root.setAttribute("data-theme",preference==="system"?(dark?"dark":"light"):preference);if(window.matchMedia("(prefers-contrast: more)").matches){root.setAttribute("data-contrast","high")}}catch(error){root.setAttribute("data-theme","dark")}})();`;
