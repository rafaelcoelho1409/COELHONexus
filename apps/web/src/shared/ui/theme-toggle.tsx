"use client";

import { Monitor, Moon, Sun } from "lucide-react";

import { nextPreference, type ThemePreference } from "@/shared/lib/theme";
import { useThemePreference } from "@/shared/lib/use-theme-preference";

const LABEL: Record<ThemePreference, string> = { system: "System", light: "Light", dark: "Dark" };
const ICON = { system: Monitor, light: Sun, dark: Moon } as const;

/** Cycles system → light → dark. The accessible name states the current choice and what a click does. */
export function ThemeToggle() {
  const [preference, setPreference] = useThemePreference();
  const Icon = ICON[preference];
  const upcoming = nextPreference(preference);

  return (
    <button
      type="button"
      onClick={() => setPreference(upcoming)}
      aria-label={`Theme: ${LABEL[preference]}. Switch to ${LABEL[upcoming]}.`}
      title={`Theme: ${LABEL[preference]}`}
      className="inline-flex size-9 items-center justify-center border border-border text-muted-foreground transition-colors hover:border-neutral-8 hover:text-foreground"
    >
      <Icon className="size-4" aria-hidden="true" />
    </button>
  );
}
