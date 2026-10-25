import { describe, expect, it } from "vitest";

import { isThemePreference, nextPreference, resolveTheme, THEME_STORAGE_KEY, themeInitScript } from "./theme";

describe("theme preference", () => {
  it("resolves 'system' from the OS and honours an explicit choice", () => {
    expect(resolveTheme("system", true)).toBe("dark");
    expect(resolveTheme("system", false)).toBe("light");
    expect(resolveTheme("light", true)).toBe("light");
    expect(resolveTheme("dark", false)).toBe("dark");
  });

  it("cycles system → light → dark → system", () => {
    expect(nextPreference("system")).toBe("light");
    expect(nextPreference("light")).toBe("dark");
    expect(nextPreference("dark")).toBe("system");
  });

  it("rejects stored values it does not know", () => {
    expect(isThemePreference("dark")).toBe(true);
    expect(isThemePreference("sepia")).toBe(false);
    expect(isThemePreference(null)).toBe(false);
  });
});

describe("themeInitScript (runs before first paint)", () => {
  function run(stored: string | null, prefersDark: boolean, prefersContrast = false) {
    const attributes: Record<string, string> = {};
    const fakeWindow = {
      localStorage: { getItem: (key: string) => (key === THEME_STORAGE_KEY ? stored : null) },
      matchMedia: (query: string) => ({
        matches: query.includes("prefers-color-scheme") ? prefersDark : prefersContrast,
      }),
    };
    const fakeDocument = {
      documentElement: { setAttribute: (name: string, value: string) => (attributes[name] = value) },
    };
    new Function("window", "document", "localStorage", themeInitScript)(
      fakeWindow,
      fakeDocument,
      fakeWindow.localStorage,
    );
    return attributes;
  }

  it("applies the stored choice, else the OS preference", () => {
    expect(run("light", true)["data-theme"]).toBe("light");
    expect(run("dark", false)["data-theme"]).toBe("dark");
    expect(run(null, true)["data-theme"]).toBe("dark");
    expect(run("garbage", false)["data-theme"]).toBe("light");
  });

  it("switches to the high-contrast tokens when the OS asks for more contrast", () => {
    expect(run(null, true, true)["data-contrast"]).toBe("high");
    expect(run(null, true, false)["data-contrast"]).toBeUndefined();
  });
});
