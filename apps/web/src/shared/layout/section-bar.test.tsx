import { render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { Section } from "./section";
import { SectionBar } from "./section-bar";

const nav = vi.hoisted(() => ({ pathname: "/dd", search: "" }));
vi.mock("next/navigation", () => ({
  usePathname: () => nav.pathname,
  useSearchParams: () => new URLSearchParams(nav.search),
}));

const SECTIONS: Section[] = [
  {
    href: "/dd",
    title: "Docs Distiller",
    description: "Ingest docs, plan distillation, study what was distilled",
    defaultTab: "catalog",
    carryParam: "slug",
    tabs: [
      { key: "catalog", label: "Catalog", href: "/dd", tab: true, carry: false },
      { key: "ingestion", label: "Ingestion", href: "/dd/ingestion", tab: true, carry: true },
      { key: "planner", label: "Planner", href: "/dd/planner", tab: false, carry: true },
    ],
  },
  { href: "/settings", title: "Settings", description: "The connections", tabs: [] },
];

function renderAt(pathname: string, search = "") {
  nav.pathname = pathname;
  nav.search = search;
  return render(<SectionBar sections={SECTIONS} />);
}

describe("SectionBar", () => {
  it("shows the page title as the one heading, with its description", () => {
    renderAt("/dd");
    expect(screen.getByRole("heading", { level: 1, name: "Docs Distiller" })).toBeInTheDocument();
    expect(screen.getByText("Ingest docs, plan distillation, study what was distilled")).toBeInTheDocument();
  });

  it("lists the stage tabs, and marks the current one with aria-current", () => {
    renderAt("/dd/ingestion");
    const tabs = within(screen.getByRole("navigation", { name: "Docs Distiller stages" }));
    expect(tabs.getAllByRole("link").map((link) => link.textContent)).toEqual(["Catalog", "Ingestion"]);
    expect(tabs.getByRole("link", { name: "Ingestion" })).toHaveAttribute("aria-current", "page");
    expect(tabs.getByRole("link", { name: "Catalog" })).not.toHaveAttribute("aria-current");
  });

  it("keeps the framework on the tabs that take it, never on the catalog", () => {
    renderAt("/dd/ingestion", "slug=next-js");
    expect(screen.getByRole("link", { name: "Ingestion" })).toHaveAttribute(
      "href",
      "/dd/ingestion?slug=next-js",
    );
    expect(screen.getByRole("link", { name: "Catalog" })).toHaveAttribute("href", "/dd");
  });

  it("shows a deep-link stage only while the reader is on it", () => {
    renderAt("/dd/planner");
    expect(screen.getByRole("link", { name: "Planner" })).toHaveAttribute("aria-current", "page");
  });

  it("has no tab strip for a section without stages", () => {
    renderAt("/settings");
    expect(screen.getByRole("heading", { level: 1, name: "Settings" })).toBeInTheDocument();
    expect(screen.queryByRole("navigation")).not.toBeInTheDocument();
  });

  it("renders nothing outside the console sections", () => {
    const { container } = renderAt("/");
    expect(container).toBeEmptyDOMElement();
  });
});
