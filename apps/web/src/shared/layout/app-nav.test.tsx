import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { AppNav } from "./app-nav";

const pathname = vi.hoisted(() => ({ current: "/" }));
vi.mock("next/navigation", () => ({ usePathname: () => pathname.current }));

function currentLinks(path: string): string[] {
  pathname.current = path;
  render(<AppNav />);
  return screen
    .getAllByRole("link")
    .filter((link) => link.getAttribute("aria-current") === "page")
    .map((link) => link.textContent ?? "");
}

describe("AppNav", () => {
  it("links both apps", () => {
    pathname.current = "/";
    render(<AppNav />);
    expect(screen.getByRole("link", { name: "Docs Distiller" })).toHaveAttribute("href", "/dd");
    expect(screen.getByRole("link", { name: "Settings" })).toHaveAttribute("href", "/settings");
  });

  it("marks a page current for its own path and every path below it", () => {
    expect(currentLinks("/dd")).toEqual(["Docs Distiller"]);
  });

  it("keeps Docs Distiller current on a stage page", () => {
    expect(currentLinks("/dd/planner")).toEqual(["Docs Distiller"]);
  });

  it("marks Settings, and nothing on a path that only shares a prefix", () => {
    expect(currentLinks("/settings")).toEqual(["Settings"]);
  });

  it("does not mistake a longer name for a child path", () => {
    expect(currentLinks("/ddx")).toEqual([]);
  });
});
