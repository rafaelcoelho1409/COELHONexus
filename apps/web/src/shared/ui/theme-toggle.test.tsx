import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it } from "vitest";

import { THEME_STORAGE_KEY } from "@/shared/lib/theme";

import { ThemeToggle } from "./theme-toggle";

beforeEach(() => {
  window.localStorage.clear();
  document.documentElement.removeAttribute("data-theme");
});

describe("ThemeToggle", () => {
  it("names the current choice and what a click does", () => {
    render(<ThemeToggle />);
    expect(screen.getByRole("button")).toHaveAccessibleName("Theme: System. Switch to Light.");
  });

  it("cycles system → light → dark → system, applying and persisting each step", async () => {
    const user = userEvent.setup();
    render(<ThemeToggle />);
    const toggle = screen.getByRole("button");

    await user.click(toggle);
    expect(document.documentElement).toHaveAttribute("data-theme", "light");
    expect(window.localStorage.getItem(THEME_STORAGE_KEY)).toBe("light");
    expect(toggle).toHaveAccessibleName("Theme: Light. Switch to Dark.");

    await user.click(toggle);
    expect(document.documentElement).toHaveAttribute("data-theme", "dark");
    expect(window.localStorage.getItem(THEME_STORAGE_KEY)).toBe("dark");

    await user.click(toggle);
    expect(window.localStorage.getItem(THEME_STORAGE_KEY)).toBe("system");
    expect(toggle).toHaveAccessibleName("Theme: System. Switch to Light.");
  });

  it("starts from a stored choice", () => {
    window.localStorage.setItem(THEME_STORAGE_KEY, "dark");
    render(<ThemeToggle />);
    expect(screen.getByRole("button")).toHaveAccessibleName("Theme: Dark. Switch to System.");
  });
});
